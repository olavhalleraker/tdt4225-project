# TDT4225 Assignment 2 – Porto taxi trajectories in MySQL

Python programs that analyse, clean and load the Porto Taxi Trajectory dataset
into MySQL 8.0.39, and answer the questions in Part 2. 

## Folder structure

| Path | Content |
|---|---|
| `src/DbConnector.py` | MySQL connection (settings from environment variables) |
| `src/cleaning.py` | Shared parsing and cleaning rules (used by both EDA and insert) |
| `src/part1_eda.py` | Part 1.1 – exploratory data analysis, writes figures to `report/figures/` |
| `src/part1_insert.py` | Part 1.2 – `CREATE TABLE` statements and insertion of the cleaned data |
| `src/part2_queries.py` | Part 2 – one method per task (MySQL queries + Python where needed) |
| `src/requirements.txt` | pip packages (the three given ones + matplotlib for the EDA figures) |
| `output/` | Console output of the programs, and CSV files with the full answers of tasks 4a, 6, 8 and 9 |
| `data/porto.csv` | The dataset.

## Setup

1. Start MySQL 8.0.39 in its own Docker container:

   ```bash
   docker run --name=tdt4225-a2-mysql -e MYSQL_ROOT_PASSWORD=<root-password> -e MYSQL_DATABASE=porto_db -e MYSQL_USER=tdt4225 -e MYSQL_PASSWORD=<password> -p 3307:3306 -d mysql:8.0.39 --innodb-buffer-pool-size=3G
   ```

2. Install the Python packages:

   ```bash
   python3.11 -m venv .venv && .venv/bin/pip install -r src/requirements.txt
   ```

3. Give the programs the database password (and optionally `DB_HOST`,
   `DB_PORT`, `DB_NAME`, `DB_USER`; defaults: localhost, 3307, porto_db, tdt4225):

   ```bash
   export DB_PASSWORD=<password>
   ```

## Run (from `src/`)

```bash
python part1_eda.py | tee ../output/part1_eda.txt
python part1_insert.py | tee ../output/part1_insert.txt
python part2_queries.py | tee ../output/part2_results.txt
