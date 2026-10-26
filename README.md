# Kinetik Bike Stock App

Logs into each mountain bike brand's B2B dealer portal, scrapes availability
(in stock / out of stock / pre-order / discontinued), and writes a normalized
CSV you can use to update Timesact Pre-Order availability on the Shopify store.

## How it works

- `config/brands.yaml` lists every brand: its portal URL, which scraper class
  handles it, and which `.env` variables hold its login credentials.
- Each brand gets its own scraper module under `src/kinetik_stock/scrapers/`
  implementing a common interface (`login()` + `fetch_stock()`), because every
  brand's B2B site has a different login flow and page layout.
- `run.py` launches a browser (Playwright/Chromium), runs every enabled
  brand's scraper, normalizes results into a common schema, and writes one CSV.

A brand failing to scrape (bad credentials, portal layout changed) doesn't
stop the others — it's reported at the end and the run exits non-zero.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # skip if already installed on this machine
cp .env.example .env          # then fill in real credentials
```

## Running

```bash
python run.py                          # scrape all enabled brands
python run.py --brands norco   # scrape just one brand
python run.py --no-headless            # show the browser window (debugging)
python run.py --output output/stock.csv
python run.py -v                       # verbose/debug logging
```

Output CSVs are written to `output/` by default, one per brand selection
(e.g. `stock_transition_bikes.csv`, `stock_all.csv` for a full run) -
overwritten on each run rather than piling up. Pass `--output` for a
different/one-off path.

## GUI (Windows + Android)

```bash
python gui.py          # or double-click start_gui.bat on Windows
```

- **On the Windows PC:** open <http://localhost:8080>.
- **On an Android phone:** connect to the same Wi-Fi and open the
  `http://<pc-ip>:8080` address printed at startup. In Chrome, menu →
  *Add to Home screen* installs it like an app.

The scrapers drive a real Chromium browser (Playwright), which can't run on
Android - so the scraping always happens on the PC, and the phone is a remote
control for it. The PC has to be on and running `gui.py`; allow Python through
Windows Firewall on private networks when prompted.

The GUI can start logins to your dealer accounts, so it needs a login: set
`KINETIK_GUI_PASSWORD` in `.env` (and optionally `KINETIK_GUI_USERNAME`, which
defaults to `sales@kinetikcycles.com`); without a password, a random one is
printed on each start. Use `python gui.py --local-only` to disable phone access. Don't expose
the port to the internet.

Tabs: **Run** (one report per brand - Norco, Devinci etc. each write their own
file, never combined; "Run all" just runs them one after another; brands still
in progress show as such and can't be run; live log), **Results** (browse/filter/download anything in `output/`), **SKU
lists** (download/replace each report's input CSV; the old one is kept as
`.csv.bak`). Dealer credentials are still edited in `.env`, not in the GUI.

## CSV schema

Kept deliberately lean - across hundreds of SKUs, extra columns add up fast:

| column           | meaning                                                        |
|------------------|------------------------------------------------------------------|
| brand            | display name from `config/brands.yaml`                          |
| sku              | brand's part number, for matching to your Shopify variant       |
| product_title    | model name, with size/color stripped out into their own columns |
| size             | e.g. "Large", "X-Large MX", "One Size" - blank if not applicable |
| color            | e.g. "Moonstone" - blank if not applicable                       |
| regular_retail   | list price, if the portal exposes one, else blank                |
| availability     | one of `in_stock`, `low_stock`, `out_of_stock`, `pre_order`, `discontinued`, `unknown` |
| eta_date         | date shown on the bike's public product page, only looked up for `pre_order` rows - `N/A` otherwise (in-stock/out-of-stock/etc. bikes don't have one) |

More detail (exact quantity for low-stock items, variant split out
separately, raw untouched portal text, source URL, scrape timestamp) is
still captured on each `StockItem` in code, just not written to the CSV.
Tell me if you want any of that back as a column, or want fewer/different
ones once you've seen how Timesact Pre-Order's import expects the data.

## Adding a real brand

1. Add credentials to `.env`:
   ```
   ACME_BIKES_USERNAME=...
   ACME_BIKES_PASSWORD=...
   ```
2. Add an entry to `config/brands.yaml`:
   ```yaml
   - key: acme_bikes
     name: "Acme Bikes"
     portal_url: "https://dealers.acmebikes.com/login"
     scraper: "kinetik_stock.scrapers.acme_bikes.AcmeBikesScraper"
     username_env: "ACME_BIKES_USERNAME"
     password_env: "ACME_BIKES_PASSWORD"
     enabled: true
   ```
3. Copy `src/kinetik_stock/scrapers/example_brand.py` (a template scraper, only used by the tests) to
   `src/kinetik_stock/scrapers/acme_bikes.py`, rename the class, and rewrite
   `login()` and `fetch_stock()` for that portal's actual login form and
   stock page/table (selectors, status wording, pagination, etc. all differ
   per brand). Run with `--no-headless -v --brands acme_bikes` first to watch
   it navigate and confirm selectors.
4. Run `python run.py --brands acme_bikes` to test it in isolation before
   adding it to a full run.

Tell me the brand name, portal URL, and (ideally) a description or
screenshots of its login page and stock page, and I'll write the scraper for
you.

## Tests

```bash
pytest
```

`tests/test_example_scraper.py` runs the full pipeline (login, scrape,
status normalization, CSV write) against the local fixture — no network or
real credentials required.
