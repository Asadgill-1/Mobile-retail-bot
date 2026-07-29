"""AI orchestrator (SPEC §3, §5; ADR-009). LLM, product search, escalation and Telegram all mocked."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
import pytest

import app.ai.orchestrator as orch
from app.llm.llm_client import LLMResponse, LLMToolCall
from app.llm.prompts import ESCALATION_REPLY
from app.products.models import Product
from app.tenants.models import Shop


def _shop() -> Shop:
    return Shop(id=uuid4(), client_id=uuid4(), name="Shop 01")


def _product() -> Product:
    return Product(
        id=uuid4(),
        shop_id=uuid4(),
        category="Mobile",
        brand="Samsung",
        model="Galaxy S24",
        condition="New",
        specs={"camera": "108MP"},
        cost_price=Decimal("1000.00"),
        selling_price=Decimal("1500.00"),
        quantity=3,
        boost_level=9,
        tags=["clearance"],
    )


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def side_effects(monkeypatch) -> dict:
    """`escalate` writes to Supabase and Telegram; `alert_owner` sends Telegram. Never in a unit test."""
    calls: dict[str, list] = {"escalate": [], "alert": []}

    async def _escalate(redis, shop, identity, message, reason, client=None):
        calls["escalate"].append({"identity": identity, "message": message, "reason": reason})

    async def _alert(shop, identity, problem, action):
        calls["alert"].append({"problem": problem, "action": action})

    monkeypatch.setattr(orch, "escalate", _escalate)
    monkeypatch.setattr(orch, "alert_owner", _alert)
    return calls


class _FakeLLM:
    """Returns queued responses; records every request it was given."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.requests: list[list] = []

    async def chat(self, messages, tools=None, tool_choice=None) -> LLMResponse:
        self.requests.append(messages)
        return self._queue.pop(0)


def _tool_call(name: str, **args) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=[LLMToolCall(id="c1", name=name, arguments=args)])


# --- the SPEC §5 leak guard: boost_level must never reach the model ---
def test_serialize_hides_boost_level_but_keeps_tags():
    payload = orch._serialize(_product())
    assert "boost_level" not in payload
    assert payload["tags"] == ["clearance"]  # model needs these to phrase "clearance deal"
    # 030: selling_price is stored ex-VAT, and a customer must never be quoted less than they pay.
    assert payload["price_aed"] == "1575"  # 1500 + 5% VAT, money as a string, never float
    assert isinstance(payload["price_aed"], str)


@pytest.mark.asyncio
async def test_search_tool_call_runs_search_then_answers(monkeypatch, redis):
    fake = _FakeLLM(
        _tool_call("search_products", requirements="good camera"),
        LLMResponse(content="We have the Galaxy S24 at 1500 AED."),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)
    seen = {}

    async def _search(shop_id, requirements, limit=5, *, max_price=None, sort="relevance", rows=None):
        seen.update(requirements=requirements, max_price=max_price, sort=sort)
        return [_product()]

    monkeypatch.setattr(orch, "search_products", _search)

    reply = await orch.answer_customer(_shop(), "p1", "phone with good camera?", redis)
    assert reply == "We have the Galaxy S24 at 1500 AED."
    assert seen["requirements"] == "good camera"
    assert seen["sort"] == "relevance" and seen["max_price"] is None  # defaults

    # the second request replayed the tool result, and it carried no boost_level
    tool_msg = next(m for m in fake.requests[1] if m.role == "tool")
    assert "boost_level" not in tool_msg.content
    assert json.loads(tool_msg.content)[0]["brand"] == "Samsung"


@pytest.mark.asyncio
async def test_show_product_media_fills_the_media_sink(monkeypatch, redis):
    """The model can search, then show that product's photos; media lands in the caller's sink."""
    prod = _product()
    prod.images = ["shop/prod/image_0.jpg", "shop/prod/image_1.jpg"]
    fake = _FakeLLM(
        _tool_call("search_products", requirements="iphone"),
        _tool_call("show_product_media", product_id=str(prod.id)),
        LLMResponse(content="Here's the iPhone in green:"),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    async def _search(shop_id, requirements, limit=5, *, max_price=None, sort="relevance", rows=None):
        return [prod]

    async def _get_product(shop_id, product_id, client=None):
        return prod  # tenant guard passes

    async def _signed(paths, ttl=3600, client=None):
        return [f"https://signed/{p}" for p in paths]

    monkeypatch.setattr(orch, "search_products", _search)
    monkeypatch.setattr("app.products.service.get_product", _get_product)
    monkeypatch.setattr("app.products.media.signed_urls", _signed)

    sink: list = []
    reply = await orch.answer_customer(_shop(), "p1", "show me the iphone", redis, media_sink=sink)
    assert reply == "Here's the iPhone in green:"
    assert sink and all(m["type"] == "photo" for m in sink)  # prod has images, no video
    assert sink[0]["url"].startswith("https://signed/")


@pytest.mark.asyncio
async def test_show_product_media_no_media_tells_model_not_to_send_to_store(monkeypatch, redis):
    """Product with no photo/video on file: the tool result must carry guidance so the model
    says we don't have one — not 'visit the store' (bug: it defaulted to the latter on sent:0)."""
    prod = _product()
    prod.images = []  # nothing on file
    prod.video_url = None
    fake = _FakeLLM(
        _tool_call("show_product_media", product_id=str(prod.id)),
        LLMResponse(content="We don't have a photo of that on file, sorry."),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    async def _get_product(shop_id, product_id, client=None):
        return prod

    async def _signed(paths, ttl=3600, client=None):
        return []

    monkeypatch.setattr("app.products.service.get_product", _get_product)
    monkeypatch.setattr("app.products.media.signed_urls", _signed)

    sink: list = []
    await orch.answer_customer(_shop(), "p1", "show me the iphone", redis, media_sink=sink)
    assert sink == []  # nothing sent
    tool_msg = next(m for m in fake.requests[-1] if getattr(m, "name", None) == "show_product_media")
    payload = json.loads(tool_msg.content)
    assert payload["sent"] == 0
    assert "note" in payload and "store" in payload["note"].lower()  # steers model off "visit store"


@pytest.mark.asyncio
async def test_escalation_short_circuits_without_answering(monkeypatch, redis, side_effects):
    fake = _FakeLLM(_tool_call("escalate_to_human", reason="refund request"))
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    async def _stock(*a, **k):  # id-reference fetch is fine; the guard below is "no 2nd LLM round"
        return []

    monkeypatch.setattr(orch, "search_products", _stock)

    reply = await orch.answer_customer(_shop(), "p1", "I want a refund", redis)
    assert reply == ESCALATION_REPLY
    assert len(fake.requests) == 1  # one LLM round → the escalation short-circuited before any tool exec
    assert side_effects["escalate"][0]["reason"] == "refund request"


@pytest.mark.asyncio
async def test_request_shop_media_notifies_and_stays_seamless(monkeypatch, redis, side_effects):
    """Customer wants photos we don't have and agrees: the shop is notified with a deterministic
    reason, and the customer gets the model's OWN line — not the 'specialist' escalation reply."""
    prod = _product()
    line = "I've asked the shop — they'll send photos shortly!"
    fake = _FakeLLM(
        _tool_call("request_shop_media", product_id=str(prod.id)),
        LLMResponse(content=line),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    async def _get_product(shop_id, product_id, client=None):
        return prod

    monkeypatch.setattr("app.products.service.get_product", _get_product)

    reply = await orch.answer_customer(_shop(), "p1", "yes please", redis)
    assert reply == line  # seamless: the model's own line, not ESCALATION_REPLY
    assert reply != ESCALATION_REPLY
    assert side_effects["escalate"][0]["reason"] == "📷 Photo/video requested: Samsung Galaxy S24"


@pytest.mark.asyncio
async def test_request_shop_media_notifies_even_on_junk_id(monkeypatch, redis, side_effects):
    """A stale/garbled id must still reach the shop — never crash the turn — with a generic reason."""
    fake = _FakeLLM(
        _tool_call("request_shop_media", product_id="not-a-uuid"),
        LLMResponse(content="Asked the shop for you!"),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    reply = await orch.answer_customer(_shop(), "p1", "yes", redis)
    assert reply == "Asked the shop for you!"
    assert side_effects["escalate"][0]["reason"] == "📷 Photo/video requested: a product"


@pytest.mark.asyncio
async def test_plain_answer_needs_no_tools(monkeypatch, redis):
    fake = _FakeLLM(LLMResponse(content="Hello! How can I help?"))
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)
    assert await orch.answer_customer(_shop(), "p1", "hi", redis) == "Hello! How can I help?"


# --- Stage 6: the conversation is remembered, and replayed ---
@pytest.mark.asyncio
async def test_turns_are_recorded_and_replayed_to_the_model(monkeypatch, redis):
    shop = _shop()
    monkeypatch.setattr(orch, "get_llm_client", lambda: _FakeLLM(LLMResponse(content="Hi there!")))
    await orch.answer_customer(shop, "p1", "hello", redis)

    # second turn: the model must see turn one
    fake = _FakeLLM(LLMResponse(content="Yes, in green."))
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)
    await orch.answer_customer(shop, "p1", "in green?", redis)

    roles_and_text = [(m.role, m.content) for m in fake.requests[0]]
    assert roles_and_text[0][0] == "system"
    assert ("user", "hello") in roles_and_text
    assert ("assistant", "Hi there!") in roles_and_text
    assert roles_and_text[-1] == ("user", "in green?")  # current turn last, exactly once


@pytest.mark.asyncio
async def test_shopkeeper_turns_replay_as_the_shops_own_voice(monkeypatch, redis):
    """After /handover the AI continues a conversation a human was holding."""
    from app.escalations.context import remember

    shop = _shop()
    await remember(redis, shop.id, "p1", "customer", "is it waterproof?")
    await remember(redis, shop.id, "p1", "shopkeeper", "Yes, IP68 rated.")

    fake = _FakeLLM(LLMResponse(content="Anything else?"))
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)
    await orch.answer_customer(shop, "p1", "great, price?", redis)

    replayed = [(m.role, m.content) for m in fake.requests[0]]
    assert ("user", "is it waterproof?") in replayed
    assert ("assistant", "Yes, IP68 rated.") in replayed  # shopkeeper spoke AS the shop


@pytest.mark.asyncio
async def test_unreadable_session_still_answers(monkeypatch, redis):
    """A broken session must degrade to a single-turn answer, not to a handoff."""

    class _BadRedis:
        async def lrange(self, *a, **k):
            raise ConnectionError("redis gone")

        async def rpush(self, *a, **k):
            raise ConnectionError("redis gone")

    monkeypatch.setattr(orch, "get_llm_client", lambda: _FakeLLM(LLMResponse(content="Hello!")))
    assert await orch.answer_customer(_shop(), "p1", "hi", _BadRedis()) == "Hello!"


# --- ADR-009: a failure must look exactly like a normal handoff to the customer ---
_MACHINE_WORDS = (
    "sorry", "trouble", "error", "ai", "bot", "system", "automated",
    "language model", "try again", "technical",
)


def _assert_no_machine_words(reply: str) -> None:
    low = f" {reply.lower()} "
    for w in _MACHINE_WORDS:
        assert f" {w} " not in low and f" {w}." not in low, f"reply leaks {w!r}: {reply!r}"


@pytest.mark.asyncio
async def test_llm_failure_hands_off_and_never_tells_the_customer(monkeypatch, redis, side_effects):
    class _Broken:
        async def chat(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(orch, "get_llm_client", lambda: _Broken())

    reply = await orch.answer_customer(_shop(), "p1", "hi", redis)
    # identical to a deliberate escalation — the customer cannot tell them apart
    assert reply == ESCALATION_REPLY
    _assert_no_machine_words(reply)
    # a real human is actually summoned, not just logged
    assert side_effects["escalate"][0]["reason"] == "system failure"
    # ...and the owner alone learns what broke, and what was done about it
    assert len(side_effects["alert"]) == 1
    assert "RuntimeError" in side_effects["alert"][0]["problem"]
    assert side_effects["alert"][0]["action"] == "handed the customer to a human"


@pytest.mark.asyncio
async def test_empty_model_response_hands_off_and_alerts_owner(monkeypatch, redis, side_effects):
    fake = _FakeLLM(LLMResponse(content=None, finish_reason="length"))
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    reply = await orch.answer_customer(_shop(), "p1", "hi", redis)
    assert reply == ESCALATION_REPLY
    _assert_no_machine_words(reply)
    assert "no content" in side_effects["alert"][0]["problem"]
    assert "length" in side_effects["alert"][0]["problem"]


@pytest.mark.asyncio
async def test_deliberate_escalation_does_not_alert_the_owner(monkeypatch, redis, side_effects):
    """A refund is business-as-usual, not a system problem. Owner must not be paged."""
    fake = _FakeLLM(_tool_call("escalate_to_human", reason="refund request"))
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)

    assert await orch.answer_customer(_shop(), "p1", "refund please", redis) == ESCALATION_REPLY
    assert side_effects["escalate"]  # a human WAS summoned
    assert side_effects["alert"] == []  # but nobody was paged


@pytest.mark.asyncio
async def test_a_failing_escalation_or_alert_still_leaves_the_customer_answered(monkeypatch, redis):
    """Both run on the reply path, both do network I/O. Neither may cost the customer their reply."""

    class _Broken:
        async def chat(self, *a, **k):
            raise RuntimeError("provider down")

    async def _explode(*a, **k):
        raise ConnectionError("telegram unreachable")

    monkeypatch.setattr(orch, "get_llm_client", lambda: _Broken())
    monkeypatch.setattr(orch, "escalate", _explode)
    monkeypatch.setattr(orch, "alert_owner", _explode)

    assert await orch.answer_customer(_shop(), "p1", "hi", redis) == ESCALATION_REPLY


def test_no_fallback_reply_constant_survives():
    """The old 'Sorry, I'm having trouble right now' advertised the machine. It must stay deleted."""
    import app.llm.prompts as prompts

    assert not hasattr(prompts, "FALLBACK_REPLY")


# --- Q-015: the price arguments must reach products/search.py ---
@pytest.mark.asyncio
async def test_price_sort_and_budget_are_passed_through(monkeypatch, redis):
    fake = _FakeLLM(
        _tool_call("search_products", requirements="phone", sort="price_asc", max_price_aed=3000),
        LLMResponse(content="Our cheapest is the Galaxy S23 at 2499 AED."),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)
    seen = {}

    async def _search(shop_id, requirements, limit=5, *, max_price=None, sort="relevance", rows=None):
        seen.update(max_price=max_price, sort=sort)
        return [_product()]

    monkeypatch.setattr(orch, "search_products", _search)
    await orch.answer_customer(_shop(), "p1", "whats your cheapest phone?", redis)
    assert seen["sort"] == "price_asc"
    # A budget is what the customer is willing to PAY, so it arrives VAT-inclusive and has to be
    # net before it can filter a catalogue stored ex-VAT — otherwise "under 3000" hides the
    # 2,900 phone that costs them 3,045.
    assert seen["max_price"] == Decimal("2857.14")


@pytest.mark.asyncio
async def test_unknown_sort_from_the_model_falls_back_to_relevance(monkeypatch, redis):
    """Models improvise enum values; never hand junk to the query layer."""
    fake = _FakeLLM(
        _tool_call("search_products", requirements="phone", sort="cheapest_first"),
        LLMResponse(content="Here you go."),
    )
    monkeypatch.setattr(orch, "get_llm_client", lambda: fake)
    seen = {}

    async def _search(shop_id, requirements, limit=5, *, max_price=None, sort="relevance", rows=None):
        seen["sort"] = sort
        return []

    monkeypatch.setattr(orch, "search_products", _search)
    await orch.answer_customer(_shop(), "p1", "cheapest?", redis)
    assert seen["sort"] == "relevance"


# --- what the model may and may not see about a product (SPEC §5; migrations 023/027) ---
def test_serialize_hides_internal_tags_and_the_price_floor():
    """`high_margin` ranks server-side but has no honest customer-facing phrasing, and a model
    that can see the floor will negotiate straight to it."""
    from decimal import Decimal
    from uuid import uuid4

    from app.ai.orchestrator import _serialize
    from app.products.models import Product

    p = Product(
        id=uuid4(), shop_id=uuid4(), category="Mobile", brand="Samsung", model="S23",
        condition="New", cost_price=Decimal("2800"), selling_price=Decimal("3400"), quantity=3,
        tags=["clearance", "high_margin", "best_camera"], boost_level=7,
    )
    p.min_price = Decimal("3000")

    data = _serialize(p)

    assert data["tags"] == ["clearance", "best_camera"]
    assert "high_margin" not in str(data)
    for leak in ("boost_level", "cost_price", "min_price"):
        assert leak not in data, f"{leak} must never reach the model"


def test_serialize_never_leaks_a_held_back_bargaining_offer():
    from decimal import Decimal
    from uuid import uuid4

    from app.ai.orchestrator import _serialize
    from app.products.models import Product

    p = Product(
        id=uuid4(), shop_id=uuid4(), category="Mobile", brand="Apple", model="16",
        condition="New", cost_price=Decimal("3000"), selling_price=Decimal("3400"), quantity=1,
    )
    p.active_offer = "Free delivery this week"

    data = _serialize(p)

    assert data["offer"] == "Free delivery this week"
    # An on_haggle offer must not reach the model with the listing: given one, it spent the card
    # in its opening reply. request_price hands it over once bargaining has actually started.
    assert "bargaining_card" not in data
    assert "sweetener" not in data


# --- the stock count is the shop's business, not the model's (competitor recon) ---
def _stocked(qty: int):
    from decimal import Decimal
    from uuid import uuid4

    from app.products.models import Product

    return Product(
        id=uuid4(), shop_id=uuid4(), category="Mobile", brand="Apple", model="16 Pro",
        color="Green", condition="New", cost_price=Decimal("3000"),
        selling_price=Decimal("3400"), quantity=qty,
    )


def test_serialize_never_reveals_the_unit_count():
    """A rival can walk the whole catalogue by asking "do you have X?" thirty times. Availability
    is a yes; the number behind it is not the customer's business unless we cannot fill their order.
    """
    from app.ai.orchestrator import _serialize

    data = _serialize(_stocked(137))  # 137 shares no digits with the 3400 price, so a hit is real

    assert data["in_stock"] is True
    assert 137 not in data.values()
    assert "137" not in str(data)  # not smuggled in as a string anywhere either
    assert "available" not in data and "quantity" not in data


def test_asking_for_a_quantity_we_have_says_enough_but_still_no_number():
    from app.ai.orchestrator import _serialize

    data = _serialize(_stocked(137), need=10)

    assert data["enough"] is True
    assert "available" not in data, "we can fill it — the count stays private"
    assert "137" not in str(data)


def test_asking_for_more_than_we_have_gives_the_true_figure():
    """The one case a real number may be spoken: they cannot have what they asked for, so they
    need the honest figure to decide."""
    from app.ai.orchestrator import _serialize

    data = _serialize(_stocked(4), need=10)

    assert data["enough"] is False
    assert data["available"] == 4


async def test_junk_need_quantity_is_ignored_rather_than_crashing_the_turn(monkeypatch):
    """Models improvise arguments. A bad quantity must degrade to a plain availability answer,
    never take the customer's turn down with it."""
    from app.ai.orchestrator import _run_tool
    from app.llm.llm_client import LLMToolCall

    async def _search(shop_id, requirements, limit=5, *, max_price=None, sort="relevance", rows=None):
        return [_stocked(5)]

    monkeypatch.setattr(orch, "search_products", _search)
    shop = SimpleNamespace(id=uuid4())

    for junk in ("lots", None, -3, 0, ""):
        call = LLMToolCall(id="c", name="search_products",
                           arguments={"requirements": "iphone", "need_quantity": junk})
        payload = json.loads(await _run_tool(call, shop, "cust"))
        assert payload[0]["in_stock"] is True
        assert "enough" not in payload[0] and "available" not in payload[0]
