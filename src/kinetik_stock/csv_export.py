from __future__ import annotations

import csv
from pathlib import Path

from kinetik_stock.models import CSV_FIELDNAMES, StockItem


def write_csv(items: list[StockItem], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for item in items:
            writer.writerow(item.as_csv_row())
