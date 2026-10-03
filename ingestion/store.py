"""Save one scraped search to Postgres: one transaction, a fixed ~8 bulk statements regardless of size."""

from datetime import date, datetime, time

import psycopg

from .config import database_url
from .CRUD import airlines, airports, flights, itineraries, itinerary_legs, price_observations, route_daily_prices


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), autocommit=True)


def _time(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def save_search(conn: psycopg.Connection, origin: str, dest: str, flight_date: date,
                scrape_date: date, scraped_at: datetime, result: dict) -> dict:
    """Returns counts: saved / skipped (legs without flight number) / unpriced / history_days."""
    usable = [f for f in result["flights"]
              if all(leg["flight_number"] and leg["airline_code"] for leg in f["legs"])]
    stats = {"saved": len(usable), "skipped": len(result["flights"]) - len(usable),
             "unpriced": sum(f["price_listed_inr"] is None and f["price_cheapest_inr"] is None for f in usable),
             "history_days": 0}
    legs = [leg for f in usable for leg in f["legs"]]

    with conn.transaction():
        airports.ensure_many(conn, {leg["from"]: leg["from_name"] for leg in legs}
                             | {leg["to"]: leg["to_name"] for leg in legs})
        airlines.ensure_many(conn, {leg["airline_code"]: leg["airline_name"] for leg in legs})

        flight_ids = flights.upsert_many(conn, [
            {"flight_number": leg["flight_number"], "airline_code": leg["airline_code"],
             "origin": leg["from"], "destination": leg["to"], "plane_type": leg["plane_type"]}
            for leg in legs
        ])

        keyed = [(itineraries.make_legs_key(f["legs"]), f) for f in usable]
        itinerary_ids = itineraries.upsert_many(conn, [
            {"legs_key": key, "origin": f["origin"], "destination": f["destination"], "stops": f["stops"]}
            for key, f in keyed
        ])

        itinerary_legs.insert_many(conn, [
            (itinerary_ids[key], i, flight_ids[(leg["flight_number"], leg["from"], leg["to"])])
            for key, f in keyed for i, leg in enumerate(f["legs"])
        ])

        price_observations.upsert_many(conn, [
            {"itinerary_id": itinerary_ids[key], "flight_date": flight_date, "scrape_date": scrape_date,
             "scraped_at": scraped_at, "dep_time": _time(f["dep_time"]), "arr_time": _time(f["arr_time"]),
             "arr_day_offset": (date.fromisoformat(f["arr_date"]) - date.fromisoformat(f["dep_date"])).days
             if f["arr_date"] and f["dep_date"] else 0,
             "duration_mins": f["duration_mins"], "price_listed_inr": f["price_listed_inr"],
             "price_cheapest_inr": f["price_cheapest_inr"], "listed_section": f["listed_section"]}
            for key, f in keyed
        ])

        insights = result["price_insights"] or {}
        if insights.get("lowest_price_inr"):
            route_daily_prices.upsert_scrape(conn, origin, dest, flight_date, scrape_date,
                                             insights["lowest_price_inr"], insights.get("typical_low_inr"),
                                             insights.get("typical_high_inr"))
        history = [(date.fromisoformat(h["date"]), h["lowest_price_inr"])
                   for h in insights.get("price_history") or []]
        route_daily_prices.insert_history_many(conn, origin, dest, flight_date, history)
        stats["history_days"] = len(history)

    return stats
