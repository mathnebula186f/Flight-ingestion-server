from uuid import UUID

import psycopg

from ._common import cursor


def make_legs_key(legs: list[dict]) -> str:
    """'6E 2193:IXC-DEL>6E 5321:DEL-COK' from legs with flight_number / from / to."""
    return ">".join(f"{leg['flight_number']}:{leg['from']}-{leg['to']}" for leg in legs)


def upsert_many(conn: psycopg.Connection, rows: list[dict]) -> dict[str, UUID]:
    """Bulk upsert in one statement. rows: legs_key, origin, destination, stops. Returns {legs_key: id}."""
    unique = {r["legs_key"]: r for r in rows}  # ON CONFLICT DO UPDATE rejects the same key twice
    if not unique:
        return {}
    vals = list(unique.values())
    with cursor(conn) as cur:
        # DO UPDATE (a no-op) so RETURNING also yields the ids of existing rows
        cur.execute(
            """INSERT INTO itineraries (legs_key, origin, destination, stops)
               SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::int[])
               ON CONFLICT (legs_key) DO UPDATE SET stops = EXCLUDED.stops
               RETURNING id, legs_key""",
            ([v["legs_key"] for v in vals], [v["origin"] for v in vals], [v["destination"] for v in vals],
             [v["stops"] for v in vals]),
        )
        return {r["legs_key"]: r["id"] for r in cur.fetchall()}
