from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
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
    eta_date: Optional[str] = None
    variant: Optional[str] = None
    raw_status_text: str = ""
    source_url: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def as_csv_row(self) -> dict:
        return {
            "brand": self.brand,
            "sku": self.sku,
            "product_title": self.product_title,
            "variant": self.variant or "",
            "status": self.status.value,
            "quantity": "" if self.quantity is None else self.quantity,
            "eta_date": self.eta_date or "",
            "raw_status_text": self.raw_status_text,
            "source_url": self.source_url,
            "scraped_at": self.scraped_at,
        }


CSV_FIELDNAMES = [
    "brand",
    "sku",
    "product_title",
    "variant",
    "status",
    "quantity",
    "eta_date",
    "raw_status_text",
    "source_url",
    "scraped_at",
]
