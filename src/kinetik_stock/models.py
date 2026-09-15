from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    ETA = "eta"
    DISCONTINUED = "discontinued"
    UNKNOWN = "unknown"


@dataclass
class StockItem:
    """One brand/model/variant's availability, normalized across B2B portals."""

    brand: str
    sku: str
    product_title: str
    status: StockStatus
    quantity: Optional[int] = None
    regular_retail_price: Optional[float] = None
    eta_date: Optional[str] = None
    variant: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    raw_status_text: str = ""
    source_url: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def as_csv_row(self) -> dict:
        # Kept deliberately lean (product name, size, color, part number,
        # price, availability) - the fuller detail above (combined variant,
        # quantity, raw portal text, timestamps) stays on the object for
        # internal use but isn't written to the CSV, which otherwise gets
        # noisy fast across hundreds of SKUs.
        return {
            "brand": self.brand,
            "sku": self.sku,
            "product_title": self.product_title,
            "size": self.size or "",
            "color": self.color or "",
            "regular_retail": (
                "" if self.regular_retail_price is None else self.regular_retail_price
            ),
            "availability": self.status.value,
            "eta_date": self.eta_date or "N/A",
        }


CSV_FIELDNAMES = [
    "brand",
    "sku",
    "product_title",
    "size",
    "color",
    "regular_retail",
    "availability",
    "eta_date",
]
