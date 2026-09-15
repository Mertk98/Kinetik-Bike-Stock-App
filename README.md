# Kinetik Bike Stock App

Logs into each mountain bike brand's B2B dealer portal, scrapes availability
(in stock / out of stock / ETA / discontinued), and writes a normalized CSV
you can use to update Timesact Pre-Order availability on the Shopify store.

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
python run.py --brands example_brand   # scrape just one brand
python run.py --no-headless            # show the browser window (debugging)
python run.py --output output/stock.csv
python run.py -v                       # verbose/debug logging
```

Output CSVs are written to `output/` by default, timestamped
(`stock_20260914T120000Z.csv`), one row per SKU/variant.

## CSV schema

Kept deliberately lean - across hundreds of SKUs, extra columns add up fast:

| column           | meaning                                                        |
|------------------|------------------------------------------------------------------|
| brand            | display name from `config/brands.yaml`                          |
| sku              | brand's part number, for matching to your Shopify variant       |
| product_title    | model name (and variant, e.g. size/color, if the portal breaks it out) |
| regular_retail   | list price, if the portal exposes one, else blank                |
| availability     | one of `in_stock`, `out_of_stock`, `eta`, `discontinued`, `unknown` |

More detail (exact quantity for low-stock items, variant split out
separately, raw untouched portal text, source URL, scrape timestamp) is
still captured on each `StockItem` in code, just not written to the CSV.
Tell me if you want any of that back as a column, or want fewer/different
ones once you've seen how Timesact Pre-Order's import expects the data.

## Included demo brand

`config/brands.yaml` ships with an `example_brand` entry that points at a
local test fixture (`tests/fixtures/example_b2b/`), not a real B2B site. It
exists so `python run.py` produces real, correct output immediately, and so
you can see the whole login → scrape → CSV pipeline work. Its "credentials"
(`demo_user` / `demo_pass` in `.env.example`) aren't secret — they're
hardcoded in the fixture HTML.

Once you add your first real brand, set `example_brand`'s `enabled: false` in
`config/brands.yaml` (or delete the entry).

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
3. Copy `src/kinetik_stock/scrapers/example_brand.py` to
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
