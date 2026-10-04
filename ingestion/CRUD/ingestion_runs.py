from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

from ._common import cursor


def start(conn: psycopg.Connection, *, run_id: str, trigger: str, github_run_url: str | None, params: dict,
          scrape_date: date, started_at: datetime, searches_total: int) -> int:
    """Record a run as 'running' (so crashed or killed runs stay visible); returns its id."""
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO ingestion_runs
                 (run_id, trigger, github_run_url, params, scrape_date, started_at, status, searches_total)
               VALUES (%s, %s, %s, %s, %s, %s, 'running', %s)
               RETURNING id""",
            (run_id, trigger, github_run_url, Jsonb(params), scrape_date, started_at, searches_total),
        )
        return cur.fetchone()["id"]


def finish(conn: psycopg.Connection, run_pk: int, *, status: str, finished_at: datetime, searches_ok: int,
           searches_failed: int, itineraries_saved: int, itineraries_unpriced: int, itineraries_skipped: int,
           errors: list[dict]) -> None:
    with cursor(conn) as cur:
        cur.execute(
            """UPDATE ingestion_runs SET
                 status = %s, finished_at = %s, searches_ok = %s, searches_failed = %s,
                 itineraries_saved = %s, itineraries_unpriced = %s, itineraries_skipped = %s, errors = %s
               WHERE id = %s""",
            (status, finished_at, searches_ok, searches_failed, itineraries_saved, itineraries_unpriced,
             itineraries_skipped, Jsonb(errors), run_pk),
        )
