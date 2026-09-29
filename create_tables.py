"""
Creates the Porto taxi schema (sql/schema.sql) in the database configured in .env.

WARNING: drops and recreates the tables gps_point, trip, taxi and call_type,
so all inserted data is lost.

Usage:
    .venv/bin/python create_tables.py
"""
import os

from DbConnector import DbConnector
from tabulate import tabulate

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "schema.sql")
TABLES = ["call_type", "taxi", "trip", "gps_point"]


def read_statements(path):
    """Split the schema file into single SQL statements (comments removed)."""
    with open(path, encoding="utf-8") as f:
        lines = []
        for line in f:
            # strip '--' comments (no string literal in the schema contains '--')
            line = line.split("--", 1)[0].rstrip()
            if line:
                lines.append(line)
    sql = "\n".join(lines)
    return [stmt.strip() for stmt in sql.split(";") if stmt.strip()]


class CreateTables:

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor

    def create_schema(self):
        for stmt in read_statements(SCHEMA_FILE):
            first_line = stmt.splitlines()[0]
            print("Executing:", first_line[:70])
            self.cursor.execute(stmt)
        self.db_connection.commit()

    def show_tables(self):
        self.cursor.execute("SHOW TABLES")
        rows = self.cursor.fetchall()
        print(tabulate(rows, headers=self.cursor.column_names))

    def show_create_tables(self):
        for table in TABLES:
            self.cursor.execute("SHOW CREATE TABLE %s" % table)
            _, ddl = self.cursor.fetchone()
            print("\n" + ddl + ";")

    def show_row_counts(self):
        rows = []
        for table in TABLES:
            self.cursor.execute("SELECT COUNT(*) FROM %s" % table)
            rows.append((table, self.cursor.fetchone()[0]))
        print(tabulate(rows, headers=["table", "rows"]))


def main():
    program = None
    try:
        program = CreateTables()
        program.create_schema()
        program.show_tables()
        program.show_create_tables()
        print()
        program.show_row_counts()
    except Exception as e:
        print("ERROR: Failed to create tables:", e)
    finally:
        if program:
            program.connection.close_connection()


if __name__ == '__main__':
    main()
