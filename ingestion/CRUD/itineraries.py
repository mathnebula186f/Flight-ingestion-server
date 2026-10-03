from uuid import UUID

import psycopg

from ._common import cursor, update_row


def make_legs_key(legs: list[dict]) -> str:
    """'6E 2193:IXC-DEL>6E 5321:DEL-COK' from legs with flight_number / from / to."""
    return ">".join(f"{leg['flight_number']}:{leg['from']}-{leg['to']}" for leg in legs)


def upsert(conn: psycopg.Connection, legs_key: str, origin: str, destination: str, stops: int) -> UUID:
    """Create the itinerary or reuse the existing one (same legs_key); returns its id."""
    with cursor(conn) as cur:
        # DO UPDATE (a no-op) so RETURNING also yields the id of an existing row
        cur.execute(
            """INSERT INTO itineraries (legs_key, origin, destination, stops)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (legs_key) DO UPDATE SET stops = EXCLUDED.stops
               RETURNING id""",
            (legs_key, origin, destination, stops),
        )
        return cur.fetchone()["id"]


def upsert_many(conn: psycopg.Connection, rows: list[dict]) -> dict[str, UUID]:
    """Bulk upsert in one statement. rows: legs_key, origin, destination, stops. Returns {legs_key: id}."""
    unique = {r["legs_key"]: r for r in rows}  # ON CONFLICT DO UPDATE rejects the same key twice
    if not unique:
        return {}
    vals = list(unique.values())
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO itineraries (legs_key, origin, destination, stops)
               SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::int[])
               ON CONFLICT (legs_key) DO UPDATE SET stops = EXCLUDED.stops
               RETURNING id, legs_key""",
            ([v["legs_key"] for v in vals], [v["origin"] for v in vals], [v["destination"] for v in vals],
             [v["stops"] for v in vals]),
        )
        return {r["legs_key"]: r["id"] for r in cur.fetchall()}


def get(conn: psycopg.Connection, itinerary_id: UUID) -> dict | None:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM itineraries WHERE id = %s", (itinerary_id,))
        return cur.fetchone()


def get_by_legs_key(conn: psycopg.Connection, legs_key: str) -> dict | None:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM itineraries WHERE legs_key = %s", (legs_key,))
        return cur.fetchone()


def list_for_route(conn: psycopg.Connection, origin: str, destination: str, *,
                   max_stops: int | None = None) -> list[dict]:
    with cursor(conn) as cur:
        cur.execute(
            """SELECT * FROM itineraries
               WHERE origin = %(origin)s AND destination = %(dest)s
                 AND (%(max_stops)s::int IS NULL OR stops <= %(max_stops)s)
               ORDER BY stops, legs_key""",
            {"origin": origin, "dest": destination, "max_stops": max_stops},
        )
        return cur.fetchall()


def update(conn: psycopg.Connection, itinerary_id: UUID, *, stops: int | None = None) -> dict | None:
    return update_row(conn, "itineraries", {"id": itinerary_id}, {"stops": stops}, {"stops"})


def delete(conn: psycopg.Connection, itinerary_id: UUID) -> bool:
    """Deletes its legs too (ON DELETE CASCADE); fails if price observations still reference it."""
    with cursor(conn) as cur:
        cur.execute("DELETE FROM itineraries WHERE id = %s", (itinerary_id,))
        return cur.rowcount > 0
