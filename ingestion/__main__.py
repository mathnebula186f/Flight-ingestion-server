"""Flight price ingestion.

  python -m ingestion run                                # all 90 routes, 1-30 days ahead
  python -m ingestion run --origins DEL BLR              # only routes departing these airports
  python -m ingestion run --routes IXC-DEL BLR-GOI       # specific routes
  python -m ingestion run --days 1-7 14 21               # days ahead (ranges allowed)
  python -m ingestion run --dates 2026-10-20 2026-10-21  # exact departure dates
  python -m ingestion run --dry-run                      # scrape + parse only, no database
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from itertools import permutations

import psycopg

from . import config, google_flights, store
from .CRUD import ingestion_runs
from .constants import MAX_ERRORS_STORED

log = logging.getLogger("ingestion")


def parse_days(tokens: list[str]) -> list[int]:
    days = []
    for t in tokens:
        lo, _, hi = t.partition("-")
        days.extend(range(int(lo), int(hi or lo) + 1))
    return sorted(set(days))


def build_routes(args) -> list[tuple[str, str]]:
    if args.routes:
        return [tuple(r.upper().split("-")) for r in args.routes]
    routes = list(permutations(config.AIRPORTS, 2))
    if args.origins:
        origins = {o.upper() for o in args.origins}
        routes = [r for r in routes if r[0] in origins]
    return routes


def setup_logging(run_id: str):
    config.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_DIR / f"run_{run_id}.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger("primp").setLevel(logging.WARNING)


def github_context() -> tuple[str, str | None]:
    """('schedule' | 'workflow_dispatch' | 'local', link to the GitHub Actions run or None)."""
    event = os.environ.get("GITHUB_EVENT_NAME")
    if not event:
        return "local", None
    env = os.environ
    return event, f"{env.get('GITHUB_SERVER_URL')}/{env.get('GITHUB_REPOSITORY')}/actions/runs/{env.get('GITHUB_RUN_ID')}"


def record_finish(conn, run_pk: int | None, status: str, counts: dict, errors: list[dict]) -> None:
    """Complete the ingestion_runs row; never let a failure here hide the run's own result."""
    if conn is None or run_pk is None:
        return
    kwargs = dict(status=status, finished_at=datetime.now(config.IST), searches_ok=counts["ok"],
                  searches_failed=counts["failed"], itineraries_saved=counts["saved"],
                  itineraries_unpriced=counts["unpriced"], itineraries_skipped=counts["skipped"],
                  errors=errors[:MAX_ERRORS_STORED])
    try:
        ingestion_runs.finish(conn, run_pk, **kwargs)
    except psycopg.OperationalError:
        try:
            with store.connect() as fresh:
                ingestion_runs.finish(fresh, run_pk, **kwargs)
        except Exception as e:
            log.error(f"could not record run status: {type(e).__name__}: {e}")
    except Exception as e:
        log.error(f"could not record run status: {type(e).__name__}: {e}")


def run(args) -> int:
    now = datetime.now(config.IST)
    run_id = now.strftime("%Y%m%d_%H%M%S")
    setup_logging(run_id)

    scrape_date = now.date()
    routes = build_routes(args)
    if args.dates:
        dates = [date.fromisoformat(d) for d in args.dates]
    else:
        dates = [scrape_date + timedelta(days=d) for d in parse_days(args.days)]
    total = len(routes) * len(dates)
    log.info(f"Run {run_id}{' (dry run)' if args.dry_run else ''}: {len(routes)} routes x {len(dates)} dates "
             f"= {total} searches, scrape_date {scrape_date}")

    conn = None if args.dry_run else store.connect()
    run_pk = None
    if conn:
        trigger, github_run_url = github_context()
        run_pk = ingestion_runs.start(
            conn, run_id=run_id, trigger=trigger, github_run_url=github_run_url,
            params={"routes": args.routes, "origins": args.origins,
                    "days": None if args.dates else args.days, "dates": args.dates},
            scrape_date=scrape_date, started_at=now, searches_total=total,
        )

    dry_results, errors = [], []
    counts = {"ok": 0, "failed": 0, "saved": 0, "unpriced": 0, "skipped": 0}
    consecutive_failures = 0
    stopped_blocked = False
    started = time.monotonic()

    try:
        for n, ((origin, dest), flight_date) in enumerate(((r, d) for r in routes for d in dates), 1):
            label = f"[{n}/{total}] {origin}->{dest} {flight_date}"
            t0 = time.monotonic()
            try:
                result = google_flights.scrape(origin, dest, flight_date.isoformat())
                scraped_at = datetime.now(config.IST)
                if args.dry_run:
                    dry_results.append({"origin": origin, "destination": dest,
                                        "flight_date": flight_date.isoformat(), **result})
                    stats = {"saved": len(result["flights"]), "skipped": 0,
                             "unpriced": sum(f["price_listed_inr"] is None and f["price_cheapest_inr"] is None
                                             for f in result["flights"]),
                             "history_days": len((result["price_insights"] or {}).get("price_history", []))}
                else:
                    try:
                        stats = store.save_search(conn, origin, dest, flight_date, scraped_at, result)
                    except psycopg.OperationalError:
                        log.warning("database connection lost, reconnecting once")
                        conn = store.connect()
                        stats = store.save_search(conn, origin, dest, flight_date, scraped_at, result)

                counts["ok"] += 1
                consecutive_failures = 0
                counts["saved"] += stats["saved"]
                counts["unpriced"] += stats["unpriced"]
                counts["skipped"] += stats["skipped"]
                prices = [p for f in result["flights"] for p in (f["price_listed_inr"], f["price_cheapest_inr"]) if p]
                log.info(f"OK   {label}: {stats['saved']} itineraries ({stats['unpriced']} unpriced"
                         f"{', ' + str(stats['skipped']) + ' skipped' if stats['skipped'] else ''}), "
                         f"min {min(prices) if prices else '-'}, history {stats['history_days']}d, "
                         f"{time.monotonic() - t0:.1f}s"
                         + (f", TAB ERRORS {result['tab_errors']}" if result["tab_errors"] else ""))
            except Exception as e:
                counts["failed"] += 1
                consecutive_failures += 1
                message = f"{type(e).__name__}: {str(e)[:300]}"
                errors.append({"route": f"{origin}-{dest}", "flight_date": flight_date.isoformat(), "error": message})
                log.error(f"FAIL {label}: {message}")
                if consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
                    log.error(f"{consecutive_failures} failures in a row - stopping (likely blocked)")
                    stopped_blocked = True
                    break

            if n < total:
                time.sleep(config.DELAY_SECONDS)
    except BaseException as e:  # crash or Ctrl+C: still mark the run, then re-raise
        errors.append({"route": None, "flight_date": None, "error": f"run crashed: {type(e).__name__}: {e}"})
        record_finish(conn, run_pk, "crashed", counts, errors)
        raise

    if args.dry_run:
        out = config.LOG_DIR / f"dryrun_{run_id}.json"
        out.write_text(json.dumps(dry_results, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"Dry-run results: {out}")

    attempted = counts["ok"] + counts["failed"]
    failure_rate = counts["failed"] / attempted if attempted else 1.0
    if stopped_blocked:
        status = "stopped_blocked"
    elif attempted < total or failure_rate > config.MAX_FAILURE_RATE:
        status = "unhealthy"
    else:
        status = "ok"

    log.info(f"Done in {(time.monotonic() - started) / 60:.1f} min [{status}]: {counts['ok']}/{total} searches OK, "
             f"{counts['failed']} failed, {counts['saved']} itineraries "
             f"({counts['unpriced']} unpriced, {counts['skipped']} skipped)")
    if status != "ok":
        log.error(f"Run {status}: {attempted}/{total} attempted, failure rate {failure_rate:.0%}")

    record_finish(conn, run_pk, status, counts, errors)
    if conn:
        conn.close()
    return 0 if status == "ok" else 1


def main():
    ap = argparse.ArgumentParser(prog="python -m ingestion")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="scrape routes x dates and save to the database")
    r.add_argument("--routes", nargs="+", metavar="ORIG-DEST", help="e.g. IXC-DEL BLR-GOI")
    r.add_argument("--origins", nargs="+", metavar="CODE", help="only routes departing these airports")
    r.add_argument("--days", nargs="+", default=[f"{config.DEFAULT_DAYS_AHEAD[0]}-{config.DEFAULT_DAYS_AHEAD[-1]}"],
                   metavar="N|A-B", help="days ahead, e.g. 1-30 or 7 14 21 (default 1-30)")
    r.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD", help="exact departure dates (overrides --days)")
    r.add_argument("--dry-run", action="store_true", help="scrape and parse only; write JSON to logs/")
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
