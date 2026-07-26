"""Drive the real pipeline at peak rate and measure where the time goes.

The question this answers: at 30 shops and ~400 messages a minute, what actually saturates first?
The LLM has headroom (`ai_max_concurrency` 40 at ~3s a call is ~13 turns/sec, double peak), so the
suspect is the Supabase path — every query runs `asyncio.to_thread`, and nothing sizes the default
executor, which is `min(32, cpu_count + 4)` threads shared by every bot in the process.

Real `process_message`, real Supabase, real Redis. Only the LLM is faked, with a fixed latency:
a real provider's variance would drown the signal we are looking for, and 400 live calls a minute
is a bill for no extra information. The fake answers the way the model does — one `search_products`
tool call, then text — so the catalogue path runs exactly as in production.

Note this deliberately measures the DB path, not the LLM semaphore: replacing the client object
skips `_gate()`. That is the point.

    python scripts/loadtest.py --rate 400 --seconds 60

Everything it writes, it removes: `messages` and `pipeline_events` rows, Redis session/rate keys,
and the `usage:*` counters the console bills from — those are snapshotted and restored, because
inflating the owner's own token metering to run a benchmark would be its own kind of bug.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

sys.path[:0] = ["src", "config", "."]

from app.db.redis_client import new_redis  # noqa: E402
from app.db.supabase_client import SupabaseTenantRepo, get_supabase  # noqa: E402
from app.llm.llm_client import LLMResponse, LLMToolCall  # noqa: E402
from app.messaging.pipeline import InboundMessage, process_message  # noqa: E402

RUN = uuid.uuid4().hex[:6]
IDENT = f"loadtest-{RUN}-"
# Above RAPID_FIRE_LIMIT (20/60s) per customer the attack detector quarantines them, which would
# measure the detector instead of the database. 50 customers keeps every one well under it.
CUSTOMERS = 50
LLM_LATENCY = 0.3

# What a customer actually says, so ranking and the id reference do representative work.
LINES = [
    "do you have iphone 16 pro in green",
    "samsung phone with good camera under 3000",
    "gaming laptop budget 4000",
    "kya redmi available hai",
    "show me something cheap for my kids",
]


class FakeLLM:
    """Answers like the real model: a search tool call, then prose once the result comes back."""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[Any], tools: Any = None, **_: Any) -> LLMResponse:
        self.calls += 1
        await asyncio.sleep(LLM_LATENCY)
        if any(getattr(m, "role", "") == "tool" for m in messages):
            return LLMResponse(content="yes we have it, 3400 AED", tokens_in=900, tokens_out=40)
        return LLMResponse(
            content=None,
            tool_calls=[LLMToolCall(id="c1", name="search_products",
                                    arguments={"requirements": "phone"})],
            tokens_in=900,
            tokens_out=20,
        )


def instrument_catalogue() -> tuple[list[float], list[float]]:
    """Time real catalogue DB reads separately from ranked searches.

    The distinction is the whole measurement. Before E1 the orchestrator read the catalogue twice
    per turn — once for the id reference, once for the customer's search. After it, the turn reads
    once and `search_products` ranks the rows it is handed, doing no I/O at all. Counting both
    under one name would report "2 reads" either way and hide the change.

    `fetch_catalogue` has to be patched in BOTH modules: the orchestrator did
    `from app.products.search import fetch_catalogue`, which binds its own reference at import, so
    patching the defining module alone misses the per-turn read entirely. The two are distinct call
    sites that each wrap the real function directly, so nothing is counted twice.
    """
    import app.ai.orchestrator as orch
    import app.products.search as search

    reads: list[float] = []
    searches: list[float] = []

    def wrap(mod: Any, name: str, into: list[float]) -> None:
        original = getattr(mod, name, None)
        if original is None:
            return

        async def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return await original(*args, **kwargs)
            finally:
                into.append(time.perf_counter() - started)

        setattr(mod, name, timed)

    wrap(search, "fetch_catalogue", reads)   # search_products' own fallback read
    wrap(orch, "fetch_catalogue", reads)     # the once-per-turn read in answer()
    wrap(orch, "search_products", searches)  # ranking; free once rows are supplied
    return reads, searches


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=int, default=400, help="messages per minute")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--shop", default="Shop 01")
    ap.add_argument(
        "--paced",
        action="store_true",
        help="route through the Pacer, as the Telegram bot does and the WhatsApp webhook must. "
             "Rapid messages from one customer coalesce into a single turn instead of queueing "
             "behind each other on the session lock.",
    )
    args = ap.parse_args()

    sb = get_supabase()
    repo = SupabaseTenantRepo(sb)
    shop = next(s for s in await repo.list_shops() if args.shop in s.name)
    redis = new_redis()

    import app.llm.llm_client as llm_mod

    llm = FakeLLM()
    llm_mod._client = llm  # every get_llm_client() in-process now returns the fake
    catalogue_ms, search_ms = instrument_catalogue()

    day = datetime.now(timezone.utc).date().isoformat()
    usage_keys = [
        f"usage:{shop.client_id}:{shop.id}:{day}:{m}"
        for m in ("messages", "ai_calls", "tokens_in", "tokens_out")
    ]
    before = {k: await redis.get(k) for k in usage_keys}

    total = max(1, args.rate * args.seconds // 60)
    gap = 60.0 / args.rate
    print(f"shop={shop.name}  rate={args.rate}/min  for {args.seconds}s  -> {total} messages")
    print(f"customers={CUSTOMERS}  fake LLM latency={LLM_LATENCY}s\n")

    latencies: list[float] = []
    actions: Counter[str] = Counter()
    errors: list[str] = []

    from app.messaging.pacing import Pacer

    pacer = Pacer()
    sent: list[str] = []

    async def one(n: int) -> None:
        identity = f"{IDENT}{n % CUSTOMERS}"
        text = LINES[n % len(LINES)]
        started = time.perf_counter()

        if args.paced:
            # What the bot does today and what the webhook must do: hand the message to this
            # conversation's worker and return. Fragments inside the debounce window become ONE
            # turn, so the same customer never contends for their own session lock.
            #
            # Deliberately NOT awaited: submit() cancels the in-flight worker when a newer message
            # arrives, so awaiting it raises CancelledError. The bot doesn't await either — a
            # webhook ACKs immediately. Latency is timed around the TURN instead, which is the
            # thing a customer actually waits for.
            async def answer(batch: str, _id=identity):
                t0 = time.perf_counter()
                try:
                    result = await process_message(InboundMessage(shop, _id, batch), redis)
                    actions[result.action] += 1
                    return result
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}")
                    actions["exception"] += 1
                    raise
                finally:
                    latencies.append(time.perf_counter() - t0)

            async def send(bubble: str):
                sent.append(bubble)

            pacer.submit(shop.id, identity, text, answer=answer, send=send)
            return

        try:
            result = await process_message(InboundMessage(shop, identity, text), redis)
            actions[result.action] += 1
        except Exception as e:  # noqa: BLE001 — an error here IS the measurement
            errors.append(f"{type(e).__name__}: {e}")
            actions["exception"] += 1
        finally:
            latencies.append(time.perf_counter() - started)

    started_at = time.perf_counter()
    tasks = []
    for n in range(total):
        due = started_at + n * gap
        now = time.perf_counter()
        if due > now:
            await asyncio.sleep(due - now)
        tasks.append(asyncio.create_task(one(n)))
    submitted_for = time.perf_counter() - started_at
    await asyncio.gather(*tasks)
    if args.paced:  # the workers outlive submission — wait for every conversation to finish
        for _ in range(2000):
            live = [c.task for c in pacer._chats.values() if c.task and not c.task.done()]
            if not live:
                break
            await asyncio.gather(*live, return_exceptions=True)
    wall = time.perf_counter() - started_at

    try:
        print("=" * 62)
        print(f"mode: {'PACED (coalescing)' if args.paced else 'direct-to-pipeline'}")
        print(f"submitted {total} over {submitted_for:.1f}s, all done at {wall:.1f}s")
        print(f"turns run {sum(actions.values())}   errors {len(errors)}")
        print(f"\naction breakdown: {dict(actions)}")
        if args.paced:
            turns = sum(actions.values())
            print(f"  {total} messages -> {turns} turns "
                  f"({total / turns if turns else 0:.1f} messages per AI call), {len(sent)} bubbles sent")
        if actions.get("locked"):
            print(f"  !! {actions['locked']} message(s) DROPPED on the session lock")
        print("\nend-to-end latency (s)")
        print(f"  p50 {pct(latencies, .50):.2f}   p95 {pct(latencies, .95):.2f}"
              f"   p99 {pct(latencies, .99):.2f}   max {max(latencies):.2f}")
        answered = actions.get("ai", 0)
        print("\ncatalogue DB reads (the ~800ms round-trip)")
        print(f"  total {len(catalogue_ms)}   PER ANSWERED TURN "
              f"{len(catalogue_ms) / answered if answered else 0:.2f}")
        print(f"  p50 {pct(catalogue_ms, .50) * 1000:.0f}ms   p95 {pct(catalogue_ms, .95) * 1000:.0f}ms"
              f"   p99 {pct(catalogue_ms, .99) * 1000:.0f}ms")
        print(f"ranked searches: {len(search_ms)} "
              f"({len(search_ms) / answered if answered else 0:.2f} per turn, "
              f"p50 {pct(search_ms, .50) * 1000:.0f}ms — free when handed rows)")
        print(f"\nfake LLM calls: {llm.calls}"
              f"  ({llm.calls / answered if answered else 0:.2f} per answered turn)")
        if errors:
            print(f"\nfirst errors: {json.dumps(errors[:3], indent=1)}")
        print("=" * 62)
        return 1 if errors or actions.get("locked") else 0
    finally:
        for k, v in before.items():
            if v is None:
                await redis.delete(k)
            else:
                await redis.set(k, v, keepttl=True)
        idents = [f"{IDENT}{i}" for i in range(CUSTOMERS)]
        for ident in idents:
            await redis.delete(
                f"session:{shop.id}:{ident}", f"rate:{ident}", f"dayrate:{ident}",
                f"quarantine:{ident}", f"lock:session:{shop.id}:{ident}",
            )
        for table in ("messages", "pipeline_events"):
            try:
                sb.table(table).delete().like("identity", f"{IDENT}%").execute()
            except Exception:  # noqa: BLE001 — best effort; the run's numbers still stand
                print(f"  (could not clean {table}; identities start {IDENT})")
        await redis.aclose()
        print("cleaned up (usage counters restored)")


sys.exit(asyncio.run(main()))
