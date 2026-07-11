"""Realistic test catalogue for AI search / ranking / promotion (SPEC §4, §5).

Media lives in the owner-supplied `pices and Video/` folder (6 photos, 2 videos =
**two** real handsets: a Galaxy S23 Ultra in green + black, and a green iPhone 16).

Why so few photos is fine: `ai/orchestrator._serialize()` never sends images to the
model — it sends category/brand/model/color/condition/specs/tags/price/stock. Photo
variety therefore adds nothing to search testing. Photos only exercise the
`/addproduct` upload path (<=5 images, one video, tenant-prefixed storage paths).
Product *variety* comes from rows and specs, below.

Storage lives in `specs`, never in `model` — the live seed row
`model="Galaxy S25 Ultra 512GB"` is a modelling mistake: `search_products` matches
spec keys/values, so a spec must be a spec.

Scenarios covered (each row's `note` says which):
  - same model, same colour, different spec      -> S23U green 256GB vs 512GB
  - same model, different colour                 -> S23U green vs black
  - same model+colour+spec, different condition  -> S23U green 256GB New vs Refurbished
  - same brand, different model                  -> S23 Ultra vs S23
  - different brand, comparable phone            -> iPhone 16 vs S23 Ultra
  - different category                           -> MacBook Air (laptop)
  - out of stock (must never be offered)         -> S23U black, quantity 0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

MEDIA_DIR = Path(__file__).resolve().parents[2] / "pices and Video"

# Owner's WhatsApp-exported filenames, mapped to what they actually show.
S23U_GREEN_BACK = "WhatsApp Image 2026-07-08 at 7.29.42 AM.jpeg"
S23U_GREEN_PEN = "WhatsApp Image 2026-07-08 at 7.29.42 AM (1).jpeg"
S23U_BLACK_PEN = "WhatsApp Image 2026-07-08 at 7.29.43 AM.jpeg"
IPHONE16_BOXED = "WhatsApp Image 2026-07-08 at 7.29.43 AM (1).jpeg"
IPHONE16_IN_HAND = "WhatsApp Image 2026-07-08 at 7.29.43 AM (2).jpeg"
IPHONE16_RENDER = "WhatsApp Image 2026-07-08 at 7.29.45 AM.jpeg"
VIDEO_A = "WhatsApp Video 2026-07-08 at 7.30.27 AM.mp4"
VIDEO_B = "WhatsApp Video 2026-07-08 at 7.30.27 AM (1).mp4"

_FIXTURE_NS = uuid5(NAMESPACE_URL, "multi-shop-chatbot/test-catalog")


@dataclass(frozen=True)
class ProductFixture:
    category: str
    brand: str
    model: str
    color: str | None
    condition: str
    specs: dict[str, str]
    cost_price: Decimal
    selling_price: Decimal
    quantity: int
    note: str  # which scenario this row exists to exercise
    boost_level: int = 0
    tags: tuple[str, ...] = ()
    is_featured: bool = False
    images: tuple[str, ...] = field(default_factory=tuple)
    video: str | None = None

    def product_id(self, shop_id: UUID) -> UUID:
        """Deterministic id → re-seeding updates in place instead of duplicating."""
        key = f"{shop_id}:{self.brand}:{self.model}:{self.color}:{self.condition}:{self.specs.get('storage', '')}"
        return uuid5(_FIXTURE_NS, key)


_S23U_SPECS = {
    "camera": "200MP",
    "battery": "5000mAh",
    "processor": "Snapdragon 8 Gen 2",
    "screen": "6.8 inch AMOLED",
    "stylus": "S Pen included",
}
_IPHONE16_SPECS = {
    "camera": "48MP",
    "battery": "3561mAh",
    "processor": "A18",
    "screen": "6.1 inch OLED",
}

CATALOG: tuple[ProductFixture, ...] = (
    # --- baseline: no boost, no tags. Everything else is measured against this row.
    ProductFixture(
        category="Mobile",
        brand="Samsung",
        model="Galaxy S23 Ultra",
        color="Green",
        condition="New",
        specs={**_S23U_SPECS, "storage": "256GB", "ram": "12GB"},
        cost_price=Decimal("3200.00"),
        selling_price=Decimal("4199.00"),
        quantity=6,
        note="baseline: unboosted, untagged",
        images=(S23U_GREEN_BACK, S23U_GREEN_PEN),
        video=VIDEO_A,
    ),
    # --- same model, same colour, DIFFERENT SPEC (storage/ram) + featured
    ProductFixture(
        category="Mobile",
        brand="Samsung",
        model="Galaxy S23 Ultra",
        color="Green",
        condition="New",
        specs={**_S23U_SPECS, "storage": "512GB", "ram": "12GB"},
        cost_price=Decimal("3600.00"),
        selling_price=Decimal("4699.00"),
        quantity=3,
        note="same model+colour as baseline, different spec; featured + premium",
        tags=("premium",),
        is_featured=True,
        images=(S23U_GREEN_BACK, S23U_GREEN_PEN),
    ),
    # --- same model, DIFFERENT COLOUR, and OUT OF STOCK (must never be offered)
    ProductFixture(
        category="Mobile",
        brand="Samsung",
        model="Galaxy S23 Ultra",
        color="Black",
        condition="New",
        specs={**_S23U_SPECS, "storage": "256GB", "ram": "12GB"},
        cost_price=Decimal("3200.00"),
        selling_price=Decimal("4199.00"),
        quantity=0,
        note="same model, different colour; quantity 0 → search must exclude it",
        images=(S23U_BLACK_PEN,),
    ),
    # --- same model+colour+spec, DIFFERENT CONDITION; heavily boosted clearance
    ProductFixture(
        category="Mobile",
        brand="Samsung",
        model="Galaxy S23 Ultra",
        color="Green",
        condition="Refurbished",
        specs={**_S23U_SPECS, "storage": "256GB", "ram": "12GB"},
        cost_price=Decimal("2100.00"),
        selling_price=Decimal("2899.00"),
        quantity=4,
        note="same model/colour/spec as baseline, different condition; boost 8 + clearance",
        boost_level=8,
        tags=("clearance", "high_margin"),
        images=(S23U_GREEN_BACK,),
    ),
    # --- SAME BRAND, DIFFERENT MODEL. No photos: none exist, and search never reads them.
    ProductFixture(
        category="Mobile",
        brand="Samsung",
        model="Galaxy S23",
        color="Green",
        condition="New",
        specs={
            "camera": "50MP",
            "battery": "3900mAh",
            "processor": "Snapdragon 8 Gen 2",
            "screen": "6.1 inch AMOLED",
            "storage": "128GB",
            "ram": "8GB",
        },
        cost_price=Decimal("1900.00"),
        selling_price=Decimal("2499.00"),
        quantity=9,
        note="same brand, different model; budget tag",
        tags=("budget",),
    ),
    # --- DIFFERENT BRAND, comparable flagship. Full media set + video.
    ProductFixture(
        category="Mobile",
        brand="Apple",
        model="iPhone 16",
        color="Green",
        condition="New",
        specs={**_IPHONE16_SPECS, "storage": "128GB"},
        cost_price=Decimal("2600.00"),
        selling_price=Decimal("3399.00"),
        quantity=7,
        note="different brand; trending + best_camera, mild boost",
        boost_level=3,
        tags=("trending", "best_camera"),
        images=(IPHONE16_BOXED, IPHONE16_IN_HAND, IPHONE16_RENDER),
        video=VIDEO_B,
    ),
    # --- cross-brand echo of the same-model-different-spec case
    ProductFixture(
        category="Mobile",
        brand="Apple",
        model="iPhone 16",
        color="Green",
        condition="New",
        specs={**_IPHONE16_SPECS, "storage": "256GB"},
        cost_price=Decimal("2900.00"),
        selling_price=Decimal("3799.00"),
        quantity=5,
        note="same model as above, different spec",
        images=(IPHONE16_RENDER,),
    ),
    # --- DIFFERENT CATEGORY: a phone query must not surface this.
    ProductFixture(
        category="Laptop",
        brand="Apple",
        model="MacBook Air M2",
        color="Silver",
        condition="New",
        specs={
            "processor": "Apple M2",
            "ram": "8GB",
            "storage": "256GB",
            "screen": "13.6 inch Retina",
            "battery": "18 hours",
        },
        cost_price=Decimal("3300.00"),
        selling_price=Decimal("4299.00"),
        quantity=2,
        note="different category; a phone query must not rank this first",
        tags=("staff_pick",),
    ),
)
