import os
import pandas as pd
from dotenv import load_dotenv
import psycopg2

load_dotenv(override=True)  # pulls server, database, username, password, port from your .env (or real env)

SERVER = os.getenv('server', 'localhost')
DBNAME = os.getenv('database', 'hammarbydata')
USER = os.getenv('username', 'postgres')
PASSWORD = os.getenv('password', 'postgres')
PORT = os.getenv('port', '5432')  # string is fine

def get_connection():
    """Create a new PostgreSQL connection."""
    return psycopg2.connect(
        host=SERVER,
        dbname=DBNAME,
        user=USER,
        password=PASSWORD,
        port=PORT,
        connect_timeout=10,
        sslmode=os.getenv('sslmode', 'disable')  # change to 'require' if you need SSL
    )

def query_executor(query: str, params: tuple | dict | None = None):
    """
    Run a SQL query.
    - SELECT returns a pandas DataFrame
    - Other statements (INSERT/UPDATE/DELETE/DDL) commit and return a short message
    """
    q = query.strip().lower()
    try:
        with get_connection() as conn:
            if q.startswith("select") or q.startswith("with"):
                # pandas handles cursor creation + fetch; params is passed through safely
                df = pd.read_sql_query(query, conn, params=params)
                return df
            else:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    affected = cur.rowcount
                # context manager commits on successful exit
                return f"OK — {affected if affected is not None else 0} row(s) affected."
    except Exception as e:
        # keep this simple; swap to logging if you prefer
        print("Error:", e)
        return None

# --- Optional helpers ---

def test_connection():
    """Quick sanity check: returns server version and current database."""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT version(), current_database();")
            ver, db = cur.fetchone()
            return {"version": ver, "database": db}
    except Exception as e:
        print("Connection failed:", e)
        return None

def get_schema_overview():
    """Lightweight schema dump (schema/table/column/type/null/default)."""
    sql = """
    SELECT
      c.table_schema AS schema,
      c.table_name   AS table,
      c.ordinal_position AS col_ordinal,
      c.column_name  AS column,
      pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
      c.is_nullable  AS nullable,
      c.column_default AS default_definition
    FROM information_schema.columns c
    JOIN pg_catalog.pg_class t
      ON t.relname = c.table_name
    JOIN pg_catalog.pg_namespace n
      ON n.nspname = c.table_schema AND n.oid = t.relnamespace
    JOIN pg_catalog.pg_attribute a
      ON a.attrelid = t.oid AND a.attname = c.column_name
    WHERE c.table_schema NOT IN ('pg_catalog','information_schema')
    ORDER BY c.table_schema, c.table_name, c.ordinal_position;
    """
    return query_executor(sql)


def query_address():
    """
    Run a SQL query.
    - SELECT returns a pandas DataFrame
    - Other statements (INSERT/UPDATE/DELETE/DDL) commit and return a short message
    """
    try:
        with get_connection() as conn:
            df = pd.read_sql_query('''select address from dbo.buildingaddress ; ''', conn,)['address'].to_list()
            return df
    except Exception as e:
        # keep this simple; swap to logging if you prefer
        print("Error:", e)
        return None