from datetime import date

import psycopg

from ._common import cursor


def upsert_scrape(conn: psycopg.Connection, origin: str, destination: str, flight_date: date, price_date: date,
                  lowest_price_inr: int, typical_low_inr: int | None = None,
                  typical_high_inr: int | None = None) -> None:
    """Our own scrape for a day: always wins, overwriting any google_history row for that day."""
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO route_daily_prices
                 (origin, destination, flight_date, price_date, lowest_price_inr,
                  typical_low_inr, typical_high_inr, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'scrape')
               ON CONFLICT (origin, destination, flight_date, price_date) DO UPDATE SET
                 lowest_price_inr = EXCLUDED.lowest_price_inr,
                 typical_low_inr = EXCLUDED.typical_low_inr,
                 typical_high_inr = EXCLUDED.typical_high_inr,
                 source = 'scrape'""",
            (origin, destination, flight_date, price_date, lowest_price_inr, typical_low_inr, typical_high_inr),
        )


def insert_history_many(conn: psycopg.Connection, origin: str, destination: str, flight_date: date,
                        history: list[tuple[date, int]]) -> None:
    """Google's history as (price_date, lowest_price_inr), in one statement; days that already have a row
    are skipped."""
    if not history:
        return
    price_dates, prices = map(list, zip(*history))
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO route_daily_prices (origin, destination, flight_date, price_date, lowest_price_inr, source)
               SELECT %s, %s, %s, d, p, 'google_history' FROM unnest(%s::date[], %s::int[]) AS h(d, p)
               ON CONFLICT (origin, destination, flight_date, price_date) DO NOTHING""",
            (origin, destination, flight_date, price_dates, prices),
        )
