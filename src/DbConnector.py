import os
import mysql.connector as mysql

class DbConnector:
    """
    Connects to the MySQL server. We run MySQL 8.0.39 locally in Docker
    (see README.md), but the same class works against the IDI virtual machine.

    The connection settings are read from environment variables.

    DB_HOST     - server address            (default "localhost")
    DB_PORT     - server port               (default 3307, our Docker container)
    DB_NAME     - database name             (default "porto_db")
    DB_USER     - MySQL user                (default "tdt4225")
    DB_PASSWORD - password for DB_USER      (required, no default)
    """

    def __init__(self,
                 HOST=os.getenv("DB_HOST", "localhost"),
                 DATABASE=os.getenv("DB_NAME", "porto_db"),
                 USER=os.getenv("DB_USER", "tdt4225"),
                 PASSWORD=os.getenv("DB_PASSWORD"),
                 PORT=int(os.getenv("DB_PORT", "3307"))):
        if PASSWORD is None:
            raise RuntimeError("Set the DB_PASSWORD environment variable before connecting.")

        # Connect to the database.
        try:
            self.db_connection = mysql.connect(host=HOST, database=DATABASE, user=USER,
                                               password=PASSWORD, port=PORT)
        except Exception as e:
            print("ERROR: Failed to connect to db:", e)
            raise

        # Get the db cursor
        self.cursor = self.db_connection.cursor()

        self.cursor.execute("SET time_zone = '+00:00'")

        print("Connected to:", self.db_connection.get_server_info())
        # get database information
        self.cursor.execute("select database();")
        database_name = self.cursor.fetchone()
        print("You are connected to the database:", database_name)
        print("-----------------------------------------------\n")

    def close_connection(self):
        # close the cursor
        self.cursor.close()
        # close the DB connection
        self.db_connection.close()
        print("\n-----------------------------------------------")
        print("Connection to %s is closed" % self.db_connection.get_server_info())
