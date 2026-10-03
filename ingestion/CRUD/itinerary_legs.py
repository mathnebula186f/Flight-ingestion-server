from uuid import UUID

import psycopg

from ._common import cursor


def add_many(conn: psycopg.Connection, itinerary_id: UUID, flight_ids: list[UUID]) -> None:
    """Add legs in order (leg_order 0, 1, ...); legs that already exist are left untouched."""
    with cursor(conn) as cur:
        cur.executemany(
            """INSERT INTO itinerary_legs (itinerary_id, leg_order, flight_id) VALUES (%s, %s, %s)
               ON CONFLICT DO NOTHING""",
            [(itinerary_id, i, flight_id) for i, flight_id in enumerate(flight_ids)],
        )


def insert_many(conn: psycopg.Connection, rows: list[tuple[UUID, int, UUID]]) -> None:
    """Bulk insert (itinerary_id, leg_order, flight_id) in one statement; existing legs are left untouched."""
    if not rows:
        return
    itinerary_ids, orders, flight_ids = map(list, zip(*rows))
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO itinerary_legs (itinerary_id, leg_order, flight_id)
               SELECT * FROM unnest(%s::uuid[], %s::int[], %s::uuid[])
               ON CONFLICT DO NOTHING""",
            (itinerary_ids, orders, flight_ids),
        )


def list_for_itinerary(conn: psycopg.Connection, itinerary_id: UUID) -> list[dict]:
    """Legs in order, with the flight's number, airline and sector."""
    with cursor(conn) as cur:
        cur.execute(
            """SELECT l.leg_order, l.flight_id, f.flight_number, f.airline_code, f.origin, f.destination,
                      f.plane_type
               FROM itinerary_legs l JOIN flights f ON f.id = l.flight_id
               WHERE l.itinerary_id = %s ORDER BY l.leg_order""",
            (itinerary_id,),
        )
        return cur.fetchall()


def replace(conn: psycopg.Connection, itinerary_id: UUID, flight_ids: list[UUID]) -> None:
    """Replace all legs of an itinerary."""
    with conn.transaction():
        delete_for_itinerary(conn, itinerary_id)
        add_many(conn, itinerary_id, flight_ids)


def delete_for_itinerary(conn: psycopg.Connection, itinerary_id: UUID) -> int:
    with cursor(conn) as cur:
        cur.execute("DELETE FROM itinerary_legs WHERE itinerary_id = %s", (itinerary_id,))
        return cur.rowcount
