import pyodbc
import os
import pandas as pd

from dotenv import load_dotenv


server = os.getenv('server')
database = os.getenv('database')  # Replace with the actual database name
username = os.getenv('username')
password = os.getenv('password')

driver = '{ODBC Driver 18 for SQL Server}'

connection_string = f'''
    DRIVER={driver};
    SERVER={server};
    DATABASE={database};
    UID={username};
    PWD={password};
    Encrypt=yes;
    TrustServerCertificate=no;
    Connection Timeout=30;
'''

def query_executor(query):
    try:
        with pyodbc.connect(connection_string) as conn:
            cursor = conn.cursor()

            if query.strip().lower().startswith("select"):
                df = pd.read_sql(query, conn)
                return df
            else:
                cursor.execute(query)
                conn.commit()
                print("Query executed successfully (no output returned).")

    except Exception as e:
        print("Error:", e)
        return None

