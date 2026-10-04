import psycopg

from ._common import cursor


def ensure_many(conn: psycopg.Connection, airports: dict[str, str]) -> None:
    """Insert {code: name} pairs that don't exist yet; existing rows are left untouched."""
    rows = [(code, name or code) for code, name in airports.items() if code]
    if not rows:
        return
    codes, names = map(list, zip(*rows))
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO airports (code, name)
               SELECT * FROM unnest(%s::text[], %s::text[])
               ON CONFLICT (code) DO NOTHING""",
            (codes, names),
        )
