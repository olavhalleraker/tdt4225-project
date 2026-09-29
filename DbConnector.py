import os

import mysql.connector as mysql
from dotenv import load_dotenv

load_dotenv()


class DbConnector:
    """
    Connects to the local MySQL server running in Docker (see docker-compose.yml).
    Connection settings are read from the .env file (see .env.example),
    but can also be passed in as arguments.

    Example:
    HOST = "tdt4225-00.idi.ntnu.no" // Your server IP address/domain name
    DATABASE = "testdb" // Database name, if you just want to connect to MySQL server, leave it empty
    USER = "testuser" // This is the user you created and added privileges for
    PASSWORD = "test123" // The password you set for said user
    """

    def __init__(self,
                 HOST=os.getenv("DB_HOST", "localhost"),
                 DATABASE=os.getenv("DB_NAME"),
                 USER=os.getenv("DB_USER"),
                 PASSWORD=os.getenv("DB_PASSWORD"),
                 PORT=int(os.getenv("DB_PORT", "3306"))):
        # Connect to the database
        try:
            self.db_connection = mysql.connect(host=HOST, database=DATABASE, user=USER, password=PASSWORD, port=PORT)
        except Exception as e:
            print("ERROR: Failed to connect to db:", e)

        # Get the db cursor
        self.cursor = self.db_connection.cursor()

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
