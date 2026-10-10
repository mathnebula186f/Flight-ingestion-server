# Flight Ingestion Server

Collects **daily flight prices for Indian domestic routes** from Google Flights and stores them in Postgres (Neon),
building the price-over-time history needed to predict the best day to book a flight.

It is not a long-running server: it is a Python package (`python -m ingestion`) that runs once per day on a schedule
(GitHub Actions) and on demand (manual trigger).

---

## Contents

- [What it collects](#what-it-collects)
- [How it works](#how-it-works)
- [Data source details](#data-source-details)
- [Known limitations](#known-limitations)
- [Database schema](#database-schema)
- [Setup](#setup)
- [Usage](#usage)
- [CRUD layer](#crud-layer)
- [Deployment (GitHub Actions)](#deployment-github-actions)
- [Operations](#operations)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)

---

## What it collects

| Item | Value |
|---|---|
| Airports | DEL, BLR, HYD, GOI, IXC, BOM, MAA, CCU, AMD, COK (10) |
| Routes | every ordered pair: 10 x 9 = **90 routes** |
| Departure dates | **1-30 days ahead** of the scrape day (configurable) |
| Searches per day | 90 x 30 = **2,700** (2 page fetches each: Best + Cheapest tab) |
| Trip type | one-way, 1 adult, economy, INR |
| Per itinerary | flight numbers, legs, times, duration, stops, plane, **listed price** (Best tab), **cheapest price** (Cheapest tab) |
| Per route + date | Google's current lowest price, typical price range, and **~60 days of price history** |

Because every departure date is scraped on many consecutive days, each itinerary gets a series of prices at
30, 29, ... 1 days before departure (`days_before_departure`), which is the core training data for prediction.

---

## How it works

```
python -m ingestion run
  for each route x departure date:
    1. google_flights.fetch_tab(..., "listed")    GET google.com/travel/flights/search  (Best tab)
    2. wait 3 s
    3. google_flights.fetch_tab(..., "cheapest")  same URL, Cheapest tab
    4. parser.parse_flights / parse_price_insights   (data embedded in the page, script.ds:1)
    5. google_flights.merge                       one row per itinerary with both prices
    6. store.save_search                          one transaction, 8 bulk SQL statements
    7. wait 3 s
  summary + exit code
```

- About **7 s per search** (two fetches + delays) plus well under 1 s of database time.
- One database connection per run; each search is saved in one transaction with a fixed number of bulk
  statements (`INSERT ... SELECT FROM unnest(...)`), independent of how many flights were found.

---

## Data source details

Google Flights embeds its search results as JSON inside the HTML page (`<script class="ds:1">`). We request the page
exactly as the website does and parse that JSON. No browser, no paid API.

### Request

`GET https://www.google.com/travel/flights/search?tfs=<query>&tfu=<tab>&hl=en&curr=INR`

- **`tfs`** - base64 protobuf describing the search. Built by `google_flights.build_tfs()` byte-for-byte as the
  website builds it. (The `fast-flights` library builds a shorter `tfs`; with it the Cheapest tab missed cheaper
  booking-site fares, e.g. IXC-DEL 2026-10-10: 5,638 vs 5,510 in the browser.)
- **`tfu`** - selects the results tab rendered into the HTML: `EgYIACABKBIiAA` = **Best**, `EgYIACACKBMiAA` =
  **Cheapest** (protobuf field 4 = 1 / 2; field 5 is a UI click counter and is irrelevant).
- Requests use `primp` with a Chrome browser fingerprint.

### Two prices per itinerary

| Column | Tab | Meaning |
|---|---|---|
| `price_listed_inr` | Best | the standard (usually airline-direct) price |
| `price_cheapest_inr` | Cheapest | lowest across booking options (often an online travel agency) |

### Payload map (`ingestion/parser.py`)

| Path | Content |
|---|---|
| `payload[2][0]` / `payload[3][0]` | "best" / "other" flight lists |
| `item[1][0][1]` | price (missing = "price unavailable") |
| `leg[22]` | `[airline code, flight number, _, airline name]` |
| `payload[5][1][1]`, `[5][4][1]`, `[5][5][1]` | current lowest price, typical range low / high |
| `payload[5][10][0]` | price history `[[epoch_ms, lowest_price], ...]` (Best tab page only) |

---

## Known limitations

These were found while testing (see `../experiment/`) and are accepted for v1.

1. **Cold searches can be incomplete.** The page only contains fares Google already has cached. Fares Google has
   not fetched recently (seen mostly for **Air India / Air India Express / Alliance Air**, and on less-searched
   routes) are either listed with **"price unavailable"** or **missing entirely**. A real browser fills them in via a
   background request (`GetShoppingResults`); plain page fetches (ours, and SerpAPI's) do not trigger it.
   - Unpriced itineraries are **stored with NULL prices**, so the flight's existence is still recorded.
   - Itineraries missing from the page cannot be recorded.
   - For route-level analysis, prefer Google's own history (`route_daily_prices`), which does not have this gap.
2. **Cheapest-tab fares vary** between fetches as booking-site fares come and go; a Cheapest price can occasionally
   exceed the Best price because the two tabs are fetched seconds apart.
3. **Undocumented source.** Google can change the page format or `tfs` encoding at any time; the parser and
   `build_tfs` would then need updating. Failures are loud (search errors, non-zero exit).
4. **Blocking.** Tested only from a residential IP. Data-centre IPs (GitHub Actions) may be blocked more often; the
   run stops after 5 consecutive failures. Proxy support is not implemented yet.
5. Legs without a flight number in Google's data cannot be identified; such itineraries are skipped and counted.

---

## Database schema

Full DDL: [`db/schema.sql`](db/schema.sql). 8 tables:

| Table | One row per | Key |
|---|---|---|
| `airports` | airport (10 seeded; connection airports auto-added, `city` NULL) | `code` |
| `airlines` | airline (auto-added) | `code` |
| `flights` | physical flight leg | `UNIQUE (flight_number, origin, destination)` e.g. `AI 2533 IXC-DEL` |
| `itineraries` | what is sold as one ticket (1 leg = nonstop, 2-3 = connection) | `UNIQUE legs_key` e.g. `6E 2193:IXC-DEL>6E 5321:DEL-COK` |
| `itinerary_legs` | leg of an itinerary, in order | `(itinerary_id, leg_order)` |
| `price_observations` | itinerary x departure date x **scrape** (every run adds rows) | `UNIQUE (itinerary_id, flight_date, scraped_at)` |
| `route_daily_prices` | route x departure date x day: lowest price on the route | `(origin, destination, flight_date, price_date)` |
| `ingestion_runs` | ingestion run (one per GitHub job): trigger, status, counts, failed searches | `id` |

Rules enforced by the code:

- `price_observations` is **append-only**: every run adds its own rows, identified by `scraped_at`, so prices that
  change within a day are kept. Two runs on the same day = two rows per itinerary; existing rows are never changed.
  Storage grows with the number of runs, not days.
- Times: `scraped_at` is a `TIMESTAMPTZ` (an exact moment). For the **IST day** of a scrape always use
  `(scraped_at AT TIME ZONE 'Asia/Kolkata')::date` - a plain `scraped_at::date` uses the session timezone (UTC on
  Neon) and puts scrapes between 00:00 and 05:30 IST on the previous day. All `DATE`/`TIME` columns are IST
  (local times of Indian airports).
- `days_before_departure` is a generated column: `flight_date - (scraped_at AT TIME ZONE 'Asia/Kolkata')::date`.
- `route_daily_prices.source`: `scrape` (our scrape that day, includes Google's typical range) always overwrites
  `google_history` (backfilled from Google's ~60-day history) for the same day, never the reverse.
- Deleting a flight/itinerary still referenced by prices fails (no orphaned data).

### Example queries

```sql
-- Price history of every itinerary on a route for one departure date
SELECT i.legs_key, p.scraped_at AT TIME ZONE 'Asia/Kolkata' AS scraped_ist, p.days_before_departure,
       p.price_listed_inr, p.price_cheapest_inr
FROM price_observations p JOIN itineraries i ON i.id = p.itinerary_id
WHERE i.origin = 'IXC' AND i.destination = 'DEL' AND p.flight_date = '2026-10-20'
ORDER BY i.legs_key, p.scraped_at;

-- Latest price of each itinerary per IST day (when a day has several scrapes)
SELECT DISTINCT ON (itinerary_id, flight_date, scrape_day)
       itinerary_id, flight_date, scrape_day, scraped_at, price_listed_inr, price_cheapest_inr
FROM (SELECT *, (scraped_at AT TIME ZONE 'Asia/Kolkata')::date AS scrape_day FROM price_observations) p
ORDER BY itinerary_id, flight_date, scrape_day, scraped_at DESC;

-- Lowest price on a route for one departure date, day by day (history + our scrapes)
SELECT price_date, lowest_price_inr, source
FROM route_daily_prices
WHERE origin = 'IXC' AND destination = 'DEL' AND flight_date = '2026-10-20'
ORDER BY price_date;

-- Recent runs: did they finish, how many searches failed, which ones
SELECT run_id, trigger, params->'origins' AS origins, status, searches_ok, searches_failed,
       itineraries_unpriced, finished_at - started_at AS took, errors
FROM ingestion_runs ORDER BY started_at DESC LIMIT 20;

-- Coverage of today's scrape: how many itineraries had no price
SELECT count(*) FILTER (WHERE price_listed_inr IS NULL AND price_cheapest_inr IS NULL) AS unpriced, count(*) AS total
FROM price_observations
WHERE (scraped_at AT TIME ZONE 'Asia/Kolkata')::date = (now() AT TIME ZONE 'Asia/Kolkata')::date;
```

---

## Setup

Requires Python 3.10+.

```powershell
cd flight_ingestion_server
python -m venv myenv
.\myenv\Scripts\activate          # macOS/Linux: source myenv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then set DATABASE_URL
```

### Database (Neon)

One Neon project with two branches:

| Branch | Used by | `DATABASE_URL` lives in |
|---|---|---|
| `main` (prod) | GitHub Actions | GitHub repo secret `DATABASE_URL` |
| dev | local runs | `.env` (never committed) |

1. Run [`db/schema.sql`](db/schema.sql) once per branch in the Neon SQL Editor (or run it on `main` and
   *Reset from parent* the dev branch).
2. Every branch needs a **compute** (the running Postgres server) to accept connections. Free-plan computes sleep
   when idle and wake automatically on the next connection.

---

## Usage

```powershell
python -m ingestion run                                # all 90 routes, 1-30 days ahead
python -m ingestion run --origins DEL BLR              # only routes departing these airports
python -m ingestion run --routes IXC-DEL BLR-GOI       # specific routes
python -m ingestion run --days 1-7 14 21               # days ahead (ranges allowed)
python -m ingestion run --dates 2026-10-20 2026-10-21  # exact departure dates (overrides --days)
python -m ingestion run --routes IXC-DEL --days 7 --dry-run   # scrape + parse only, no database
```

`--days 7` means "departures 7 days from today" (one date), not seven dates.

Output:

- `logs/run_<timestamp>.log` - one line per search:
  `OK [12/270] IXC->DEL 2026-10-10: 8 itineraries (0 unpriced), min 5638, history 61d, 4.4s`
- `logs/dryrun_<timestamp>.json` - full parsed results (dry runs only)

---

## CRUD layer

`ingestion/CRUD/` has one module per table, containing only the writes ingestion needs. Each `*_many` function writes
all rows in **one statement** (`INSERT ... SELECT FROM unnest(...)`); `store.py` calls them inside one transaction.

| Module | Functions |
|---|---|
| `airports`, `airlines` | `ensure_many` (insert missing, leave existing) |
| `flights` | `upsert_many` -> `{(flight_number, origin, destination): id}` |
| `itineraries` | `make_legs_key`, `upsert_many` -> `{legs_key: id}` |
| `itinerary_legs` | `insert_many` |
| `price_observations` | `insert_many` (append-only, one row per itinerary per scrape) |
| `route_daily_prices` | `upsert_scrape`, `insert_history_many` |

Read queries (for the model / application server) will be added when those are built.

---

## Deployment (GitHub Actions)

Workflow: [`.github/workflows/ingest.yml`](.github/workflows/ingest.yml)

- **Schedule:** daily at **14:00 IST** (`cron: "30 8 * * *"`, UTC). Afternoon was chosen because test searches then
  had more complete fares (Air India group, Cheapest-tab prices) than early-morning ones. GitHub may start scheduled
  runs some minutes late.
- **Manual run:** Actions tab -> "Flight ingestion" -> **Run workflow**, with inputs `routes`, `origins`, `days`,
  `dates`, `dry_run`. Also possible via `gh workflow run ingest.yml -f routes="IXC-DEL" -f days="1-7"` or the
  GitHub REST API (`POST .../actions/workflows/ingest.yml/dispatches`), e.g. from a future dashboard button.
- **Jobs:** a `plan` job decides the shards; `ingest` runs one job per shard in parallel (`fail-fast: false`, so one
  failing airport does not cancel the others; 90-minute timeout each):
  - `routes` given -> 1 job (`--routes ...`)
  - otherwise -> **one job per departure airport** (`--origins <code>`): all 10, or those listed in `origins`.
    A GitHub job is limited to 6 hours; the full run (2,700 searches x ~7 s ~ 5.3 h) split 10 ways is ~32 min/job.
- **Concurrency:** only one ingestion at a time; a new run waits for the running one.
- **Logs:** printed to the Actions run page and uploaded as artifacts `logs-<shard>` (kept 14 days). The repo is
  public, so these are public too - they contain only routes, prices and counts.
- **Secret:** `DATABASE_URL` (Neon `main` branch) in repo Settings -> Secrets and variables -> Actions. Secret values
  cannot be viewed after saving, are masked in logs, and are not passed to workflows from fork pull requests.
- **Cost:** public repo -> standard GitHub-hosted runners are free. (A private repo's free minutes would not cover
  ~10 jobs x ~32 min per day.)
- **60-day rule:** GitHub disables scheduled workflows in public repos after 60 days without repository activity
  (it emails a warning first). Any commit resets it; re-enable from the Actions tab if it happens.
- A job with > 20 % failed searches exits non-zero, so GitHub marks it failed and emails the repo owner;
  details are in `ingestion_runs`.

---

## Operations

| Setting (`ingestion/config.py`) | Value | Purpose |
|---|---|---|
| `DEFAULT_DAYS_AHEAD` | 1-30 | departure dates per route |
| `DELAY_SECONDS` | 3 | between tab fetches and between searches |
| `MAX_CONSECUTIVE_FAILURES` | 5 | stop the run (likely blocked) |
| `MAX_FAILURE_RATE` | 0.2 | above this the run exits non-zero |

- Logging: every line goes to stdout (captured by GitHub Actions) and, locally, to `logs/run_<run_id>.log`.
- Run history: each non-dry run writes an `ingestion_runs` row - `running` at start, then `ok`, `unhealthy`
  (> 20 % failed or not all searches attempted), `stopped_blocked` (5 failures in a row) or `crashed`
  (exception / Ctrl+C) at the end, with counts, trigger (`schedule` / `workflow_dispatch` / `local`), a link to the
  GitHub Actions run, and the failed searches in `errors` (max 500).
- A failed search (network error, blocked page, parse error) is logged and skipped; the run continues.
- A lost database connection is re-opened once per search.
- To remove a bad run's prices: `DELETE FROM price_observations WHERE scraped_at BETWEEN '<run start>' AND '<run end>';`
  (times from `ingestion_runs.started_at` / `finished_at`).

---

## Project structure

```
flight_ingestion_server/
├── .github/workflows/ingest.yml  # daily schedule + manual runs on GitHub Actions
├── db/schema.sql                 # tables + seeded airports
├── ingestion/
│   ├── __main__.py               # CLI: argument parsing, run loop, logging, exit code
│   ├── config.py                 # airports, days, delays, DATABASE_URL
│   ├── google_flights.py         # build_tfs, fetch Best/Cheapest tabs, merge
│   ├── parser.py                 # Google payload -> itineraries, price insights
│   ├── store.py                  # save one search (bulk, one transaction)
│   └── CRUD/                     # one module per table
├── requirements.txt
├── .env.example
└── README.md
```

---

## Roadmap

### 1. Ingestion (this repo) - remaining

- [ ] Code review
- [ ] First real run against the dev branch (3 routes), verify tables, re-run to verify idempotency
- [ ] Larger local run (e.g. 10 routes x 3 dates): timing, coverage, blocking
- [ ] Run `schema.sql` on the `main` branch
- [ ] GitHub repo + `.github/workflows/ingest.yml` (schedule + manual inputs + 10-job matrix)
- [ ] First small cloud run: check whether Google blocks GitHub's IPs; add proxy support only if needed
- [ ] Enable the full daily run; watch the first week (failures, unpriced %, run time)

### 2. Prediction model (next)

Goal: given a route and departure date, draw the **expected price curve** from today until departure and mark the
**cheapest day to book** ("book now" vs "wait").

- Explore data: Google's ~60-day route history (`route_daily_prices`) is available from the first run;
  per-itinerary history (`price_observations`) accumulates daily.
- Baseline: average price curve by `days_before_departure` per route, scaled to today's price.
- Model: gradient boosting (XGBoost/LightGBM) predicting price at each future `days_before_departure`.
  Candidate features: route, days before departure, weekday of flight and of scrape, holidays/festivals,
  today's price vs Google's typical range, recent price trend, stops, airline.
- Evaluation: train on earlier departure dates, test on later ones; measure price error and
  "predicted cheapest day vs actual cheapest day".
- Stage 1 route-level (Google history), stage 2 per-itinerary (after ~30+ days of our own data).
