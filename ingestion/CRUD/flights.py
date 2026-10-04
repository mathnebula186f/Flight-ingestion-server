from uuid import UUID

import psycopg

from ._common import cursor


def upsert_many(conn: psycopg.Connection, rows: list[dict]) -> dict[tuple[str, str, str], UUID]:
    """Bulk upsert in one statement. rows: flight_number, airline_code, origin, destination, plane_type.
    Returns {(flight_number, origin, destination): id}."""
    unique: dict[tuple, dict] = {}
    for r in rows:
        key = (r["flight_number"], r["origin"], r["destination"])
        if key not in unique or (r.get("plane_type") and not unique[key].get("plane_type")):
            unique[key] = r  # ON CONFLICT DO UPDATE rejects the same key twice in one statement
    if not unique:
        return {}
    vals = list(unique.values())
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO flights (flight_number, airline_code, origin, destination, plane_type)
               SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[])
               ON CONFLICT (flight_number, origin, destination)
               DO UPDATE SET plane_type = COALESCE(EXCLUDED.plane_type, flights.plane_type)
               RETURNING id, flight_number, origin, destination""",
            ([v["flight_number"] for v in vals], [v["airline_code"] for v in vals], [v["origin"] for v in vals],
             [v["destination"] for v in vals], [v.get("plane_type") for v in vals]),
        )
        return {(r["flight_number"], r["origin"], r["destination"]): r["id"] for r in cur.fetchall()}
