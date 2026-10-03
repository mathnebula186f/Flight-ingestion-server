from uuid import UUID

import psycopg

from ._common import cursor, update_row


def upsert(conn: psycopg.Connection, flight_number: str, airline_code: str, origin: str, destination: str,
           plane_type: str | None = None) -> UUID:
    """Create the flight or reuse the existing one (same number + sector); returns its id."""
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO flights (flight_number, airline_code, origin, destination, plane_type)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (flight_number, origin, destination)
               DO UPDATE SET plane_type = COALESCE(EXCLUDED.plane_type, flights.plane_type)
               RETURNING id""",
            (flight_number, airline_code, origin, destination, plane_type),
        )
        return cur.fetchone()["id"]


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


def get(conn: psycopg.Connection, flight_id: UUID) -> dict | None:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM flights WHERE id = %s", (flight_id,))
        return cur.fetchone()


def get_by_number(conn: psycopg.Connection, flight_number: str, origin: str, destination: str) -> dict | None:
    with cursor(conn) as cur:
        cur.execute(
            "SELECT * FROM flights WHERE flight_number = %s AND origin = %s AND destination = %s",
            (flight_number, origin, destination),
        )
        return cur.fetchone()


def list_all(conn: psycopg.Connection, *, airline_code: str | None = None, origin: str | None = None,
             destination: str | None = None, limit: int = 100) -> list[dict]:
    with cursor(conn) as cur:
        cur.execute(
            """SELECT * FROM flights
               WHERE (%(airline)s::text IS NULL OR airline_code = %(airline)s)
                 AND (%(origin)s::text IS NULL OR origin = %(origin)s)
                 AND (%(dest)s::text IS NULL OR destination = %(dest)s)
               ORDER BY flight_number LIMIT %(limit)s""",
            {"airline": airline_code, "origin": origin, "dest": destination, "limit": limit},
        )
        return cur.fetchall()


def update(conn: psycopg.Connection, flight_id: UUID, *, plane_type: str | None = None) -> dict | None:
    return update_row(conn, "flights", {"id": flight_id}, {"plane_type": plane_type}, {"plane_type"})


def delete(conn: psycopg.Connection, flight_id: UUID) -> bool:
    """Fails if the flight is still used by an itinerary leg."""
    with cursor(conn) as cur:
        cur.execute("DELETE FROM flights WHERE id = %s", (flight_id,))
        return cur.rowcount > 0
