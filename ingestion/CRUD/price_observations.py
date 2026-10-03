from datetime import date, datetime, time
from uuid import UUID

import psycopg

from ._common import cursor, update_row

UPDATABLE = {"dep_time", "arr_time", "arr_day_offset", "duration_mins",
             "price_listed_inr", "price_cheapest_inr", "listed_section"}


def upsert(conn: psycopg.Connection, *, itinerary_id: UUID, flight_date: date, scrape_date: date,
           scraped_at: datetime, dep_time: time, arr_time: time, arr_day_offset: int, duration_mins: int,
           price_listed_inr: int | None, price_cheapest_inr: int | None,
           listed_section: str | None) -> int:
    """One row per itinerary + departure date + scrape day. A same-day re-run refreshes the row
    but never replaces a known price with NULL. Returns the row id."""
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO price_observations
                 (itinerary_id, flight_date, scrape_date, scraped_at, dep_time, arr_time, arr_day_offset,
                  duration_mins, price_listed_inr, price_cheapest_inr, listed_section)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (itinerary_id, flight_date, scrape_date) DO UPDATE SET
                 scraped_at = EXCLUDED.scraped_at,
                 dep_time = EXCLUDED.dep_time,
                 arr_time = EXCLUDED.arr_time,
                 arr_day_offset = EXCLUDED.arr_day_offset,
                 duration_mins = EXCLUDED.duration_mins,
                 price_listed_inr = COALESCE(EXCLUDED.price_listed_inr, price_observations.price_listed_inr),
                 price_cheapest_inr = COALESCE(EXCLUDED.price_cheapest_inr, price_observations.price_cheapest_inr),
                 listed_section = COALESCE(EXCLUDED.listed_section, price_observations.listed_section)
               RETURNING id""",
            (itinerary_id, flight_date, scrape_date, scraped_at, dep_time, arr_time, arr_day_offset,
             duration_mins, price_listed_inr, price_cheapest_inr, listed_section),
        )
        return cur.fetchone()["id"]


def upsert_many(conn: psycopg.Connection, rows: list[dict]) -> int:
    """Bulk version of upsert() in one statement (same rules). rows: dicts with upsert()'s keyword names.
    Returns the number of rows written."""
    unique: dict[tuple, dict] = {}
    for r in rows:  # ON CONFLICT DO UPDATE rejects the same key twice; merge, keeping known prices
        key = (r["itinerary_id"], r["flight_date"], r["scrape_date"])
        if key in unique:
            for col in ("price_listed_inr", "price_cheapest_inr", "listed_section"):
                unique[key][col] = unique[key][col] if unique[key][col] is not None else r[col]
        else:
            unique[key] = dict(r)
    if not unique:
        return 0
    vals = list(unique.values())
    cols = ["itinerary_id", "flight_date", "scrape_date", "scraped_at", "dep_time", "arr_time", "arr_day_offset",
            "duration_mins", "price_listed_inr", "price_cheapest_inr", "listed_section"]
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO price_observations
                 (itinerary_id, flight_date, scrape_date, scraped_at, dep_time, arr_time, arr_day_offset,
                  duration_mins, price_listed_inr, price_cheapest_inr, listed_section)
               SELECT * FROM unnest(%s::uuid[], %s::date[], %s::date[], %s::timestamptz[], %s::time[], %s::time[],
                                    %s::int[], %s::int[], %s::int[], %s::int[], %s::text[])
               ON CONFLICT (itinerary_id, flight_date, scrape_date) DO UPDATE SET
                 scraped_at = EXCLUDED.scraped_at,
                 dep_time = EXCLUDED.dep_time,
                 arr_time = EXCLUDED.arr_time,
                 arr_day_offset = EXCLUDED.arr_day_offset,
                 duration_mins = EXCLUDED.duration_mins,
                 price_listed_inr = COALESCE(EXCLUDED.price_listed_inr, price_observations.price_listed_inr),
                 price_cheapest_inr = COALESCE(EXCLUDED.price_cheapest_inr, price_observations.price_cheapest_inr),
                 listed_section = COALESCE(EXCLUDED.listed_section, price_observations.listed_section)""",
            [[v[c] for v in vals] for c in cols],
        )
        return cur.rowcount


def get(conn: psycopg.Connection, observation_id: int) -> dict | None:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM price_observations WHERE id = %s", (observation_id,))
        return cur.fetchone()


def list_for_itinerary(conn: psycopg.Connection, itinerary_id: UUID, *,
                       flight_date: date | None = None) -> list[dict]:
    """Price history of one itinerary, oldest scrape first (optionally for one departure date)."""
    with cursor(conn) as cur:
        cur.execute(
            """SELECT * FROM price_observations
               WHERE itinerary_id = %(it)s AND (%(fd)s::date IS NULL OR flight_date = %(fd)s)
               ORDER BY flight_date, scrape_date""",
            {"it": itinerary_id, "fd": flight_date},
        )
        return cur.fetchall()


def list_for_route(conn: psycopg.Connection, origin: str, destination: str, flight_date: date, *,
                   scrape_date: date | None = None) -> list[dict]:
    """All itineraries' prices on a route for one departure date (optionally one scrape day)."""
    with cursor(conn) as cur:
        cur.execute(
            """SELECT p.*, i.legs_key, i.stops
               FROM price_observations p JOIN itineraries i ON i.id = p.itinerary_id
               WHERE i.origin = %(o)s AND i.destination = %(d)s AND p.flight_date = %(fd)s
                 AND (%(sd)s::date IS NULL OR p.scrape_date = %(sd)s)
               ORDER BY p.scrape_date, COALESCE(p.price_cheapest_inr, p.price_listed_inr) NULLS LAST""",
            {"o": origin, "d": destination, "fd": flight_date, "sd": scrape_date},
        )
        return cur.fetchall()


def update(conn: psycopg.Connection, observation_id: int, **values) -> dict | None:
    return update_row(conn, "price_observations", {"id": observation_id}, values, UPDATABLE)


def delete(conn: psycopg.Connection, observation_id: int) -> bool:
    with cursor(conn) as cur:
        cur.execute("DELETE FROM price_observations WHERE id = %s", (observation_id,))
        return cur.rowcount > 0


def delete_for_scrape_date(conn: psycopg.Connection, scrape_date: date) -> int:
    """Remove everything scraped on one day (e.g. to redo a bad run)."""
    with cursor(conn) as cur:
        cur.execute("DELETE FROM price_observations WHERE scrape_date = %s", (scrape_date,))
        return cur.rowcount
