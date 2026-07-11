"""Ranking algorithm (SPEC §4, §5). Pure — no DB, no LLM."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.products.models import Product
from app.products.search import rank, relevance, score

_SHOP = uuid4()


def _p(model: str, **kw) -> Product:
    base = dict(
        id=uuid4(),
        shop_id=_SHOP,
        category="Mobile",
        brand="Samsung",
        model=model,
        condition="New",
        cost_price=Decimal("1000.00"),
        selling_price=Decimal("1500.00"),
        quantity=1,
    )
    return Product(**{**base, **kw})


def test_relevance_counts_specs_once_and_tags_double():
    p = _p("Galaxy", specs={"camera": "108MP"}, tags=["best_camera"])
    # "camera" hits the spec text (1) and the tag text (2) → 3
    assert relevance(p, {"camera"}) == 3
    # "108mp" hits only the spec value
    assert relevance(p, {"108mp"}) == 1
    assert relevance(p, {"unrelated"}) == 0


def test_boost_multiplies_relevance():
    plain = _p("A", specs={"camera": "108MP"})
    boosted = _p("B", specs={"camera": "108MP"}, boost_level=10)
    tokens = {"camera"}
    assert score(plain, tokens) == 1.0
    assert score(boosted, tokens) == 2.0  # relevance 1 * (1 + 10/10)


def test_boosted_product_outranks_equally_relevant_plain_one():
    plain = _p("Plain", specs={"camera": "108MP"})
    boosted = _p("Boosted", specs={"camera": "108MP"}, boost_level=5)
    assert [p.model for p in rank([plain, boosted], "good camera")] == ["Boosted", "Plain"]


def test_relevance_beats_boost_when_boost_cannot_close_the_gap():
    """A boost-10 product with 1 match (score 2.0) must not outrank 3 matches (score 3.0)."""
    boosted = _p("Boosted", specs={"camera": "108MP"}, boost_level=10)
    relevant = _p("Relevant", specs={"camera": "108MP", "battery": "5000mAh", "ram": "12GB"})
    assert [p.model for p in rank([boosted, relevant], "camera battery ram")] == [
        "Relevant",
        "Boosted",
    ]


def test_vague_request_prioritizes_featured_then_boost():
    """SPEC §5: nothing matches → all scores 0 → featured first, then boost."""
    plain = _p("Plain")
    boosted = _p("Boosted", boost_level=7)
    featured = _p("Featured", is_featured=True)
    ordered = rank([plain, boosted, featured], "hello what do you have")
    assert [p.model for p in ordered] == ["Featured", "Boosted", "Plain"]


def test_brand_is_searchable():
    samsung = _p("Galaxy")
    apple = _p("iPhone", brand="Apple")
    assert rank([samsung, apple], "apple phone")[0].model == "iPhone"


def test_rank_respects_limit():
    products = [_p(f"M{i}") for i in range(10)]
    assert len(rank(products, "anything", limit=3)) == 3


# --- Q-015: price ordering and synonyms. A boosted product must never hide a cheaper one. ---
from decimal import Decimal as _D  # noqa: E402

from app.products.search import _tokens  # noqa: E402


def _priced(model: str, price: str, **kw) -> Product:
    return _p(model, selling_price=_D(price), **kw)


def test_cheapest_ignores_boost_the_q015_regression():
    """The exact bug: a boost-8 refurb at 2899 hid a budget phone at 2499."""
    boosted = _priced("Refurb S23U", "2899.00", boost_level=8, tags=["clearance"])
    cheap = _priced("Galaxy S23", "2499.00", tags=["budget"])
    featured = _priced("S23U 512", "4699.00", is_featured=True)
    catalog = [boosted, featured, cheap]

    # relevance sort: boost wins — this is what produced the wrong answer
    assert rank(catalog, "phone", sort="relevance")[0].model != "Galaxy S23"
    # price_asc: truth wins, regardless of boost
    assert rank(catalog, "phone", sort="price_asc")[0].model == "Galaxy S23"
    assert rank(catalog, "phone", sort="price_desc")[0].model == "S23U 512"


def test_price_sort_still_respects_what_the_customer_asked_for():
    """'cheapest samsung' must not return a cheaper Apple."""
    cheap_apple = _priced("iPhone SE", "1200.00", brand="Apple")
    samsung_a = _priced("Galaxy S23", "2499.00")
    samsung_b = _priced("Galaxy S23 Ultra", "4199.00")
    top = rank([cheap_apple, samsung_b, samsung_a], "cheapest samsung", sort="price_asc")[0]
    assert top.brand == "Samsung" and top.model == "Galaxy S23"


def test_price_sort_with_no_match_falls_back_to_whole_catalogue():
    """'cheapest' alone matches no spec — must still order the whole pool by price."""
    a = _priced("A", "3000.00")
    b = _priced("B", "1000.00")
    assert rank([a, b], "cheapest", sort="price_asc")[0].model == "B"


def test_max_price_filters_out_everything_above_the_budget():
    a = _priced("A", "3000.00")
    b = _priced("B", "1000.00")
    assert [p.model for p in rank([a, b], "phone", max_price=_D("1500"))] == ["B"]
    assert rank([a, b], "phone", max_price=_D("500")) == []


def test_synonyms_map_customer_words_onto_the_schema():
    assert "mobile" in _tokens("do you have a phone")       # category is `Mobile`
    assert "budget" in _tokens("something cheap")           # tag is `budget`
    assert "clearance" in _tokens("any deal on this")       # tag is `clearance`
    assert "laptop" in _tokens("a notebook for work")


def test_phone_query_now_matches_mobile_category():
    mobile = _p("Galaxy", category="Mobile")
    laptop = _p("MacBook", category="Laptop", brand="Apple")
    assert relevance(mobile, _tokens("a phone")) > relevance(laptop, _tokens("a phone"))
