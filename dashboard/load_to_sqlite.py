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

# Merged views, and the per-cell tables each one is built from.
# The prediction tables are written by train_soh.py and
# train_rul.py, so on a checkout that has only run the data
# pipeline they are simply not there yet.
MERGED_VIEWS = {
    "soh_predictions_all": "soh_predictions",
    "rul_predictions_all": "rul_predictions",
}

CELLS = ["B0005", "B0006", "B0007", "B0018"]


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

    loaded = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    # SQLite does not resolve the names in a CREATE VIEW until
    # the view is read, so a view over a missing table is
    # created without complaint and only fails later, from
    # whatever queries it. Each view is therefore built from
    # the member tables that are actually present.
    for view_name, prefix in MERGED_VIEWS.items():
        members = [
            f"{prefix}_{cell}"
            for cell in CELLS
            if f"{prefix}_{cell}" in loaded
        ]

        if not members:
            print(
                f"  {view_name:40s}  skipped, no {prefix}_* tables found"
            )
            continue

        select = " UNION ALL ".join(
            f"SELECT * FROM [{member}]"
            for member in members
        )

        conn.execute(
            f"CREATE VIEW IF NOT EXISTS [{view_name}] AS {select}"
        )

        if len(members) < len(CELLS):
            missing = sorted(
                set(CELLS)
                - {
                    member.rsplit("_", 1)[1]
                    for member in members
                }
            )
            print(
                f"  {view_name:40s}  built from {len(members)} cells, "
                f"missing {', '.join(missing)}"
            )
        else:
            print(
                f"  {view_name:40s}  built from {len(members)} cells"
            )

    conn.commit()

    # verify
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' OR type='view' ORDER BY name")
    tables = cursor.fetchall()
    print(f"\nDatabase: {DB_PATH}")
    print(f"Tables/views: {len(tables)}")
    for t in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0]
        except sqlite3.Error as error:
            # Reported rather than raised, so one unreadable
            # object does not hide the state of the rest.
            print(f"  {t[0]:40s}  unreadable: {error}")
            continue
        print(f"  {t[0]:40s}  {count:>6} rows")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
