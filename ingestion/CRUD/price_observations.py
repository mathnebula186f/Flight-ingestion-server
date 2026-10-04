import psycopg

from ._common import cursor

COLUMNS = ["itinerary_id", "flight_date", "scraped_at", "dep_time", "arr_time", "arr_day_offset",
           "duration_mins", "price_listed_inr", "price_cheapest_inr", "listed_section"]


def insert_many(conn: psycopg.Connection, rows: list[dict]) -> int:
    """Bulk insert in one statement; rows are dicts keyed by COLUMNS.
    Every scrape adds its own rows (identified by scraped_at); existing rows are never changed.
    Returns the number of rows inserted."""
    unique: dict[tuple, dict] = {}
    for r in rows:  # same itinerary twice in one search: merge, keeping known prices
        key = (r["itinerary_id"], r["flight_date"], r["scraped_at"])
        if key in unique:
            for col in ("price_listed_inr", "price_cheapest_inr", "listed_section"):
                if unique[key][col] is None:
                    unique[key][col] = r[col]
        else:
            unique[key] = dict(r)
    if not unique:
        return 0
    vals = list(unique.values())
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO price_observations
                 (itinerary_id, flight_date, scraped_at, dep_time, arr_time, arr_day_offset,
                  duration_mins, price_listed_inr, price_cheapest_inr, listed_section)
               SELECT * FROM unnest(%s::uuid[], %s::date[], %s::timestamptz[], %s::time[], %s::time[],
                                    %s::int[], %s::int[], %s::int[], %s::int[], %s::text[])
               ON CONFLICT (itinerary_id, flight_date, scraped_at) DO NOTHING""",
            [[v[c] for v in vals] for c in COLUMNS],
        )
        return cur.rowcount
