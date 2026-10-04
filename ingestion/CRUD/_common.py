import psycopg
from psycopg.rows import dict_row


def cursor(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)
