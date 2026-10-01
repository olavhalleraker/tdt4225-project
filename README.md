# TDT4225 Assignment 2 – Porto Taxi Trajectories (group 16)

MySQL database and queries for the Porto Taxi Trajectory dataset.
The report is `report/report.tex`, and it is the source of the PDF we hand in.

## How to run

1. **Data.** Put the dataset CSV in `data/porto.csv`. It is not in the repository, since it is too large.
2. **Settings.** Copy `.env.example` to `.env` and fill in the passwords.
3. **Database.** Start MySQL 8.0.39 in Docker:
   ```
   docker compose up -d
   ```
4. **Python.**
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
5. **EDA** (Part 1):
   ```
   .venv/bin/python eda/eda.py              # all checks
   .venv/bin/python eda/eda.py 3 13         # only check 3 and 13
   .venv/bin/python eda/eda.py --figures    # save the plots to eda/figures/
   ```
6. **Create the tables and load the data** (Part 1). `create_tables.py` drops and recreates the tables.
   ```
   .venv/bin/python create_tables.py
   .venv/bin/python insert_data.py
   ```
7. **Queries** (Part 2):
   ```
   .venv/bin/python queries/part2_q1_5.py       # tasks 1-5
   .venv/bin/python queries/part2_q6_10.py      # tasks 6-10
   .venv/bin/python queries/part2_q6_10.py 6    # only task 6
   ```
   The full result lists are written to `queries/output/`.

## Files

| Path | Content |
|---|---|
| `DbConnector.py` | Connection to MySQL, settings from `.env` |
| `sql/schema.sql` | CREATE TABLE statements |
| `create_tables.py` | Creates the tables from `sql/schema.sql` |
| `insert_data.py` | Cleans the CSV and loads it into the database |
| `eda/eda.py` | Exploratory data analysis, one numbered check per finding |
| `queries/` | The Part 2 programs and their output |
| `report/` | The report in LaTeX, synced with Overleaf |
| `docs/` | Working notes (schema, results, report draft) |
