import psycopg

from ._common import cursor, update_row


def create(conn: psycopg.Connection, code: str, name: str, city: str | None = None) -> dict:
    with cursor(conn) as cur:
        cur.execute("INSERT INTO airports (code, name, city) VALUES (%s, %s, %s) RETURNING *", (code, name, city))
        return cur.fetchone()


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


def get(conn: psycopg.Connection, code: str) -> dict | None:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM airports WHERE code = %s", (code,))
        return cur.fetchone()


def list_all(conn: psycopg.Connection) -> list[dict]:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM airports ORDER BY code")
        return cur.fetchall()


def update(conn: psycopg.Connection, code: str, *, name: str | None = None, city: str | None = None) -> dict | None:
    return update_row(conn, "airports", {"code": code}, {"name": name, "city": city}, {"name", "city"})


def delete(conn: psycopg.Connection, code: str) -> bool:
    with cursor(conn) as cur:
        cur.execute("DELETE FROM airports WHERE code = %s", (code,))
        return cur.rowcount > 0
