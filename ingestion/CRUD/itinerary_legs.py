from uuid import UUID

import psycopg

from ._common import cursor


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
