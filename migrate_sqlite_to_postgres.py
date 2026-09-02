#!/usr/bin/env python3
"""
One-shot migration: copies every table from the old SQLite database
(algorithmic_enterprise.db) into the new PostgreSQL database.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/algorithmic python3 migrate_sqlite_to_postgres.py [path/to/sqlite.db]

Run main.py once first (so the PostgreSQL schema exists), then run this.
Safe to re-run: it skips rows whose primary key already exists in PostgreSQL.
"""
import json
import os
import sqlite3
import sys

import psycopg2

SQLITE_FILE = sys.argv[1] if len(sys.argv) > 1 else "algorithmic_enterprise.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/algorithmic")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# table -> ordered columns to copy (id included so relationships survive)
TABLES = {
    "institutes":       ["id", "institute_name", "full_name", "email", "password_hash", "password_salt", "created_at"],
    "staff_users":      ["id", "institute_id", "full_name", "email", "password_hash", "password_salt", "permission", "created_at"],
    "branches":         ["id", "institute_id", "name"],
    "students":         ["id", "branch_id", "name", "email", "batch", "status", "document", "roll_number", "parent_contact"],
    "teachers":         ["id", "branch_id", "name", "subject", "department", "document", "contact_number"],
    "classrooms":       ["id", "branch_id", "room_no", "capacity", "building", "document"],
    "syllabus":         ["id", "branch_id", "subject", "semester", "units", "document", "topic", "teacher_name", "num_lectures", "lecture_date"],
    "attendance":       ["id", "branch_id", "student_name", "date", "status", "document"],
    "timetables_slots": ["id", "branch_id", "batch_name", "day", "time_slot", "subject", "teacher", "room"],
    "invigilation":     ["id", "branch_id", "teacher_name", "exam_date", "room", "document"],
    "fees":             ["id", "branch_id", "student_name", "amount_inr", "status", "due_date", "document"],
}


def main():
    if not os.path.exists(SQLITE_FILE):
        sys.exit(f"SQLite file not found: {SQLITE_FILE}")

    lite = sqlite3.connect(SQLITE_FILE)
    lite.row_factory = sqlite3.Row
    pg = psycopg2.connect(DATABASE_URL)
    pg_cur = pg.cursor()

    for table, columns in TABLES.items():
        try:
            rows = lite.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"  - {table}: not present in SQLite, skipped")
            continue

        copied = 0
        for row in rows:
            values = [row[c] if c in row.keys() else None for c in columns]
            placeholders = ", ".join(["%s"] * len(columns))
            pg_cur.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
                values,
            )
            copied += pg_cur.rowcount
        # old staff users get sensible new defaults for the designation system
        if table == "staff_users":
            pg_cur.execute(
                "UPDATE staff_users SET designation = 'admin', modules = %s WHERE designation = 'custom' AND modules = '[]'",
                (json.dumps(['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'timetables', 'invigilation', 'fees']),),
            )
        # keep the SERIAL sequences ahead of the migrated ids
        pg_cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )
        print(f"  - {table}: {copied}/{len(rows)} rows copied")

    pg.commit()
    pg.close()
    lite.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
