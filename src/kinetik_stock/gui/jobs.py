"""Background job runner for the web GUI.

Scrapes take minutes and drive a real browser, so they run on one worker
thread while the GUI polls for progress. Only one job runs at a time: the
brand portals are logged into with real dealer accounts, and parallel
sessions risk kicking each other out.
"""
from __future__ import annotations

import importlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from kinetik_stock.config import REPO_ROOT, load_brand_configs
from kinetik_stock.csv_export import write_csv

OUTPUT_DIR = REPO_ROOT / "output"

# Brands with their own per-brand availability report (driven by the user's
# own input CSV of SKUs) - mirrors norco_report.py / devinci_report.py /
# santa_cruz_report.py.
# key -> (module, scraper class, writer fn, input-CSV constant, output name)
REPORTS = {
    "norco": (
        "kinetik_stock.scrapers.norco", "NorcoScraper",
        "write_norco_report_csv", "NORCO_ITEMS_CSV", "norco_report.csv",
    ),
    "devinci": (
        "kinetik_stock.scrapers.devinci", "DevinciScraper",
        "write_devinci_report_csv", "DEVINCI_ITEMS_CSV", "devinci_report.csv",
    ),
    "santa_cruz": (
        "kinetik_stock.scrapers.santa_cruz", "SantaCruzScraper",
        "write_santa_cruz_report_csv", "SANTA_CRUZ_ITEMS_CSV", "santa_cruz_report.csv",
    ),
}


class Busy(RuntimeError):
    """A job is already running."""


@dataclass
class Job:
    label: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "queued"  # queued | running | done | failed
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    log: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)  # file names inside output/, one per brand
    rows: int = 0
    errors: list[str] = field(default_factory=list)
    # One entry per brand: {key, name, ok, file, rows, error}
    results: list[dict] = field(default_factory=list)

    def say(self, line: str) -> None:
        self.log.append(line)

    def to_dict(self, log_from: int = 0) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "outputs": self.outputs,
            "rows": self.rows,
            "errors": self.errors,
            "results": self.results,
            "log": self.log[log_from:],
            "log_len": len(self.log),
        }


class _JobLogHandler(logging.Handler):
    """Copies log records emitted by the worker thread into the job log."""

    def __init__(self, job: Job, thread_id: int):
        super().__init__(logging.INFO)
        self.job, self.thread_id = job, thread_id
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread_id:
            self.job.say(self.format(record))


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Optional[Job] = None

    def start(self, label: str, work: Callable[[Job], None]) -> Job:
        with self._lock:
            if self.current and self.current.status in ("queued", "running"):
                raise Busy(f"'{self.current.label}' is still running")
            job = Job(label=label)
            self.current = job
        threading.Thread(target=self._run, args=(job, work), daemon=True).start()
        return job

    def _run(self, job: Job, work: Callable[[Job], None]) -> None:
        job.status = "running"
        pkg_logger = logging.getLogger("kinetik_stock")
        old_level = pkg_logger.level
        handler = _JobLogHandler(job, threading.get_ident())
        pkg_logger.setLevel(logging.INFO)
        pkg_logger.addHandler(handler)
        try:
            work(job)
            job.status = "failed" if (job.errors and not job.outputs) else "done"
        except Exception as exc:  # surfaced in the GUI instead of killing the thread
            logging.getLogger(__name__).exception("Job failed")
            job.errors.append(str(exc))
            job.status = "failed"
        finally:
            pkg_logger.removeHandler(handler)
            pkg_logger.setLevel(old_level)
            job.finished = time.time()


def brand_info() -> list[dict]:
    out = []
    for b in load_brand_configs():
        out.append(
            {
                "key": b.key,
                "name": b.name,
                "enabled": b.enabled,
                "credentials_present": b.credentials_present(),
                "missing": b.missing_credential_envs(),
                "coming_soon": b.coming_soon,
                "report": b.key in REPORTS,
            }
        )
    return out


def input_csv_path(key: str) -> Path:
    module, _cls, _writer, const, _out = REPORTS[key]
    return getattr(importlib.import_module(module), const)


def _run_stock(key: str, headless: bool) -> tuple[str, int]:
    """Brands without a per-brand report format: that brand's own stock CSV."""
    from kinetik_stock.runner import run_all

    items, errors = run_all(only_keys=[key], headless=headless)
    if errors:
        raise RuntimeError("; ".join(m for _k, m in errors))
    name = f"stock_{key}.csv"
    write_csv(items, OUTPUT_DIR / name)
    return name, len(items)


def _run_report(key: str, headless: bool) -> tuple[str, int]:
    from playwright.sync_api import sync_playwright

    from kinetik_stock.browser import launch_chromium

    module, cls, writer, _const, out_name = REPORTS[key]
    mod = importlib.import_module(module)
    brand = next(b for b in load_brand_configs() if b.key == key)
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        try:
            scraper = getattr(mod, cls)(brand, browser, headless=headless)
            scraper.login()
            try:
                rows = scraper.generate_availability_report(input_csv_path(key))
            finally:
                scraper.close()
        finally:
            browser.close()
    getattr(mod, writer)(rows, OUTPUT_DIR / out_name)
    return out_name, len(rows)


def make_brand_work(keys: list[str], headless: bool = True) -> Callable[[Job], None]:
    """Runs each brand on its own and writes one report file per brand -
    a failure in one brand doesn't stop the rest. Every brand gets an entry
    in job.results saying whether it produced a file or why not."""

    def work(job: Job) -> None:
        brands = {b.key: b for b in load_brand_configs()}
        for key in keys:
            brand = brands[key]
            job.say(f"=== {brand.name} ===")
            result = {"key": key, "name": brand.name, "ok": False, "file": None, "rows": 0, "error": None}
            job.results.append(result)
            try:
                if not brand.credentials_present():
                    raise RuntimeError(
                        f"Missing credentials: set {', '.join(brand.missing_credential_envs())} in .env"
                    )
                name, rows = (_run_report if key in REPORTS else _run_stock)(key, headless)
                result.update(ok=True, file=name, rows=rows)
                job.outputs.append(name)
                job.rows += rows
                job.say(f"Wrote {rows} rows to output/{name}")
            except Exception as exc:
                logging.getLogger(__name__).exception("Failed: %s", brand.name)
                result["error"] = str(exc) or exc.__class__.__name__
                job.errors.append(f"{brand.name}: {result['error']}")

    return work
