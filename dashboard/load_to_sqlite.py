"""
load_to_sqlite.py
------------------
Load all processed CSV files into a SQLite database for Grafana.
"""

import os
import sqlite3
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data", "processed")
DB_PATH = os.path.join(BASE, "data", "battery_pdm.db")


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)

    csv_files = sorted(f for f in os.listdir(DATA) if f.endswith(".csv"))

    for csv_file in csv_files:
        table_name = csv_file.replace(".csv", "").replace("-", "_")
        path = os.path.join(DATA, csv_file)

        df = pd.read_csv(path)
        df.to_sql(table_name, conn, if_exists="replace", index=False)

        print(f"  {table_name:40s}  {len(df):>6} rows  {len(df.columns):>3} cols")

    # create a view that merges SOH predictions from all cells
    conn.execute("""
        CREATE VIEW IF NOT EXISTS soh_predictions_all AS
        SELECT * FROM soh_predictions_B0005
        UNION ALL SELECT * FROM soh_predictions_B0006
        UNION ALL SELECT * FROM soh_predictions_B0007
        UNION ALL SELECT * FROM soh_predictions_B0018
    """)

    # create a view that merges RUL predictions from all cells
    conn.execute("""
        CREATE VIEW IF NOT EXISTS rul_predictions_all AS
        SELECT * FROM rul_predictions_B0005
        UNION ALL SELECT * FROM rul_predictions_B0006
        UNION ALL SELECT * FROM rul_predictions_B0007
        UNION ALL SELECT * FROM rul_predictions_B0018
    """)

    conn.commit()

    # verify
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' OR type='view' ORDER BY name")
    tables = cursor.fetchall()
    print(f"\nDatabase: {DB_PATH}")
    print(f"Tables/views: {len(tables)}")
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0]
        print(f"  {t[0]:40s}  {count:>6} rows")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
