"""Inline keyboards + callback-data vocabulary for the owner / keeper / rider bots.

Pure builders — no handlers, no bot state, no I/O. Imports only `telegram`, so both the
handler layer (`bot.py`, attaching keyboards to menus and lists) and the outbound senders
(`orders/service.py` → `notify.send_to_*`, attaching action buttons to push notifications)
can import it with no cycle back through `bot.py`.

callback_data is `action:arg:arg`, ASCII, always ≤ 64 bytes (Telegram's hard limit). Action
codes are short on purpose so an order number + a rider UUID still fit. `parse_cb` splits it back.
The dispatchers in `bot.py` route on the action code and reuse the SAME service calls the slash
commands use — a button is a second entry point, never a second implementation.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

CB_LIMIT = 64  # Telegram callback_data max bytes


def cb(*parts: object) -> str:
    """Join callback-data parts with ':' and assert Telegram's 64-byte limit (fail loud in tests)."""
    data = ":".join(str(p) for p in parts)
    assert len(data.encode()) <= CB_LIMIT, f"callback_data too long ({len(data)}): {data!r}"
    return data


def parse_cb(data: str) -> tuple[str, list[str]]:
    """'act:1:2' → ('act', ['1','2'])."""
    action, _, rest = data.partition(":")
    return action, (rest.split(":") if rest else [])


# Shared fixed-choice rows ---------------------------------------------------
# Report/profit periods and export filters are the only "arguments" that are a closed set,
# so they become buttons; everything free-text (reason, price, phone) is a typed prompt instead.
_PERIODS = [("Today", "today"), ("Yesterday", "yesterday"), ("This week", "weekly"),
            ("This month", "monthly")]


def _period_rows(action: str) -> list[list[B]]:
    return [[B(label, callback_data=cb(action, val))] for label, val in _PERIODS]


# --- RIDER ------------------------------------------------------------------
def rider_menu() -> M:
    return M([
        [B("📦 My deliveries", callback_data=cb("rmydel"))],
        [B("📊 My report", callback_data=cb("rrepmenu"))],
    ])


def rider_report_menu() -> M:
    return M(_period_rows("rrep") + [[B("⬅️ Menu", callback_data=cb("rmenu"))]])


def rider_delivery_actions(order_number: int, custody: str, status: str) -> M | None:
    """Buttons for ONE assignment, keyed to its state. None once it's delivered/terminal."""
    if status == "delivered":
        return None
    if custody == "accepted":  # picked up → can complete or cancel
        return M([[B("🚚 Deliver", callback_data=cb("rdel", order_number)),
                   B("🚫 Cancel", callback_data=cb("rcan", order_number))]])
    if custody == "disputed":  # already said "not received" — nothing to do
        return None
    return M([[B("✅ Accept (I have it)", callback_data=cb("racc", order_number)),
               B("❌ Not received", callback_data=cb("rnrx", order_number))]])


# --- KEEPER -----------------------------------------------------------------
def keeper_menu() -> M:
    return M([
        [B("📥 Order drafts", callback_data=cb("korders")),
         B("💰 Price requests", callback_data=cb("kpr"))],
        [B("🛵 Riders", callback_data=cb("kriders")),
         B("💵 Reconcile COD", callback_data=cb("krecmenu"))],
        [B("📈 Profit", callback_data=cb("kprofmenu")),
         B("📊 Product stats", callback_data=cb("kstats"))],
        [B("📤 Export orders", callback_data=cb("kexpmenu")),
         B("💬 Negotiation", callback_data=cb("knegmenu"))],
        [B("🏷 Product tools", callback_data=cb("kprodmenu"))],
    ])


def keeper_profit_menu() -> M:
    return M(_period_rows("kprof") + [[B("⬅️ Menu", callback_data=cb("kmenu"))]])


def keeper_export_menu() -> M:
    filts = [("Today", "today"), ("Yesterday", "yesterday"), ("Pending", "pending"), ("All", "all")]
    rows = [[B(l, callback_data=cb("kexp", v)), B(f"{l} (detailed)", callback_data=cb("kexpd", v))]
            for l, v in filts]
    return M(rows + [[B("⬅️ Menu", callback_data=cb("kmenu"))]])


def keeper_negotiation_menu() -> M:
    return M([[B("💬 Turn ON", callback_data=cb("kneg", "on")),
               B("🔒 Turn OFF", callback_data=cb("kneg", "off"))],
              [B("⬅️ Menu", callback_data=cb("kmenu"))]])


def keeper_product_menu() -> M:
    """Product edits need an id + value typed, so each button starts a guided prompt."""
    return M([
        [B("🚀 Boost", callback_data=cb("kboost")), B("↩️ Unboost", callback_data=cb("kunboost"))],
        [B("🏷 Add tags", callback_data=cb("ktag")), B("🏷 Remove tag", callback_data=cb("kuntag"))],
        [B("🧹 Clear tags", callback_data=cb("kcleartags")),
         B("⭐ Toggle feature", callback_data=cb("kfeature"))],
        [B("⬅️ Menu", callback_data=cb("kmenu"))],
    ])


def keeper_order_actions(order_number: int) -> M:
    """On each pending draft (list + push notification)."""
    return M([[B("✅ Confirm", callback_data=cb("kconf", order_number)),
               B("❌ Reject", callback_data=cb("krej", order_number))]])


def keeper_delivery_menu(order_number: int) -> M:
    """Fulfilment step buttons + assign-rider for a confirmed order."""
    return M([[B("📦 Packed", callback_data=cb("kdup", order_number, "packed")),
               B("🚚 Shipped", callback_data=cb("kdup", order_number, "shipped")),
               B("✅ Delivered", callback_data=cb("kdup", order_number, "delivered"))],
              [B("🛵 Assign rider", callback_data=cb("kasg", order_number))]])


def keeper_price_actions(request_number: int) -> M:
    """On each pending price request (list + push notification)."""
    return M([[B("✅ Approve", callback_data=cb("kappr", request_number)),
               B("✏️ Counter", callback_data=cb("kcust", request_number)),
               B("❌ Decline", callback_data=cb("kdeny", request_number))]])


def keeper_rider_picker(order_number: int, riders: list[dict]) -> M:
    """Pick which rider gets this order — one button per rider (assignment carries the UUID)."""
    rows = [[B(f"🛵 {r['name']}", callback_data=cb("kasgr", order_number, r["id"]))] for r in riders]
    return M(rows + [[B("⬅️ Cancel", callback_data=cb("kmenu"))]])


def keeper_reconcile_picker(riders: list[dict]) -> M:
    rows = [[B(f"💵 {r['name']}", callback_data=cb("krec", r["id"]))] for r in riders]
    return M(rows + [[B("⬅️ Menu", callback_data=cb("kmenu"))]])


# --- OWNER ------------------------------------------------------------------
def owner_menu() -> M:
    return M([
        [B("🏪 Shops", callback_data=cb("oshops")),
         B("📊 Dashboard", callback_data=cb("odash"))],
        [B("💰 Profit", callback_data=cb("oprofmenu")),
         B("🩺 Health", callback_data=cb("ohealth"))],
        [B("🚦 Escalations", callback_data=cb("oesc")),
         B("🛡 Security", callback_data=cb("osecmenu"))],
        [B("📋 Audit", callback_data=cb("oaudit"))],
    ])


def owner_profit_menu() -> M:
    rows = _period_rows("oprof")
    rows.append([B("↔️ Compare shops", callback_data=cb("oprof", "compare"))])
    return M(rows + [[B("⬅️ Menu", callback_data=cb("omenu"))]])


def owner_shop_picker(shops: list[dict]) -> M:
    rows = [[B(f"🏪 {s['name']}", callback_data=cb("oshop", s["id"]))] for s in shops]
    return M(rows + [[B("⬅️ Menu", callback_data=cb("omenu"))]])


def owner_shop_actions(shop_id: str) -> M:
    return M([
        [B("⏸ Pause", callback_data=cb("opause", shop_id)),
         B("▶️ Resume", callback_data=cb("oresume", shop_id))],
        [B("📋 Status", callback_data=cb("ostatus", shop_id)),
         B("🛵 Riders", callback_data=cb("oriders", shop_id))],
        [B("➕ Add rider", callback_data=cb("oaddr", shop_id))],
        [B("⬅️ Shops", callback_data=cb("oshops"))],
    ])


def owner_security_menu() -> M:
    """Every security action needs a phone or incident id typed, so each starts a prompt."""
    return M([
        [B("🔎 Investigate", callback_data=cb("oinv")),
         B("🚫 Blacklist", callback_data=cb("oblk"))],
        [B("🔓 Lift quarantine", callback_data=cb("oqlift")),
         B("⏲ Extend quarantine", callback_data=cb("oqext"))],
        [B("📨 Forward to shop", callback_data=cb("ofwd"))],
        [B("🤖 Bypass AI", callback_data=cb("obyp")),
         B("↩️ Remove bypass", callback_data=cb("obypr"))],
        [B("⬅️ Menu", callback_data=cb("omenu"))],
    ])


if __name__ == "__main__":
    # self-check: round-trip + every builder stays under the 64-byte callback limit.
    assert parse_cb(cb("kasgr", 7, "550e8400-e29b-41d4-a716-446655440000")) == (
        "kasgr", ["7", "550e8400-e29b-41d4-a716-446655440000"])
    assert parse_cb("kmenu") == ("kmenu", [])
    fake = [{"id": "550e8400-e29b-41d4-a716-446655440000", "name": "Ali"}]
    for kb in (rider_menu(), rider_report_menu(), keeper_menu(), keeper_profit_menu(),
               keeper_export_menu(), keeper_negotiation_menu(), keeper_product_menu(),
               keeper_order_actions(7), keeper_delivery_menu(7), keeper_price_actions(3),
               keeper_rider_picker(7, fake), keeper_reconcile_picker(fake), owner_menu(),
               owner_profit_menu(), owner_shop_picker(fake), owner_shop_actions(fake[0]["id"]),
               owner_security_menu(), rider_delivery_actions(7, "offered", "shipped")):
        for row in kb.inline_keyboard:
            for btn in row:
                assert len(btn.callback_data.encode()) <= CB_LIMIT, btn.callback_data
    assert rider_delivery_actions(7, "none", "delivered") is None
    print("keyboards self-check ok")
