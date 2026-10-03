from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def cursor(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def update_row(conn: psycopg.Connection, table: str, keys: dict[str, Any],
               values: dict[str, Any], allowed: set[str]) -> dict | None:
    """UPDATE <table> SET <values> WHERE <keys> RETURNING *. Only columns in `allowed` may be set."""
    values = {k: v for k, v in values.items() if v is not None}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"cannot update {table}.{sorted(unknown)}")
    if not values:
        raise ValueError("nothing to update")

    query = sql.SQL("UPDATE {table} SET {sets} WHERE {where} RETURNING *").format(
        table=sql.Identifier(table),
        sets=sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in values),
        where=sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in keys),
    )
    with cursor(conn) as cur:
        cur.execute(query, [*values.values(), *keys.values()])
        return cur.fetchone()
