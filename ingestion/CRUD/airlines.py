import psycopg

from ._common import cursor, update_row


def create(conn: psycopg.Connection, code: str, name: str) -> dict:
    with cursor(conn) as cur:
        cur.execute("INSERT INTO airlines (code, name) VALUES (%s, %s) RETURNING *", (code, name))
        return cur.fetchone()


def ensure_many(conn: psycopg.Connection, airlines: dict[str, str]) -> None:
    """Insert {code: name} pairs that don't exist yet; existing rows are left untouched."""
    rows = [(code, name or code) for code, name in airlines.items() if code]
    if not rows:
        return
    codes, names = map(list, zip(*rows))
    with cursor(conn) as cur:
        cur.execute(
            """INSERT INTO airlines (code, name)
               SELECT * FROM unnest(%s::text[], %s::text[])
               ON CONFLICT (code) DO NOTHING""",
            (codes, names),
        )


def get(conn: psycopg.Connection, code: str) -> dict | None:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM airlines WHERE code = %s", (code,))
        return cur.fetchone()


def list_all(conn: psycopg.Connection) -> list[dict]:
    with cursor(conn) as cur:
        cur.execute("SELECT * FROM airlines ORDER BY code")
        return cur.fetchall()


def update(conn: psycopg.Connection, code: str, *, name: str | None = None) -> dict | None:
    return update_row(conn, "airlines", {"code": code}, {"name": name}, {"name"})


def delete(conn: psycopg.Connection, code: str) -> bool:
    with cursor(conn) as cur:
        cur.execute("DELETE FROM airlines WHERE code = %s", (code,))
        return cur.rowcount > 0
