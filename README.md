# ALGORITHMIC — Enterprise Institutional Operations

FastAPI + PostgreSQL platform for running an institute: students, teachers,
classrooms, syllabus, attendance, timetables, invigilation duty and fees —
multi-branch, multi-user, with designation-based access control.

## Requirements

- Python 3.10+
- A PostgreSQL server (13+)

## Setup

```bash
pip install -r requirements.txt

# Point the app at your PostgreSQL database (postgres:// URLs also accepted):
export DATABASE_URL="postgresql://user:password@host:5432/algorithmic"

uvicorn main:app --host 0.0.0.0 --port 8000
```

If `DATABASE_URL` is not set, the app tries
`postgresql://postgres:postgres@localhost:5432/algorithmic`.
Tables are created automatically on first start.

## Migrating from the old SQLite database

If you have data in the previous `algorithmic_enterprise.db` file:

```bash
# 1. start main.py once so the PostgreSQL schema exists, then:
DATABASE_URL="postgresql://..." python3 migrate_sqlite_to_postgres.py algorithmic_enterprise.db
```

## Features

- **Dashboard analytics** — attendance this week per batch, 7-day attendance
  trend, fees collected vs pending, top pending fees, and the lectures that
  are ongoing in each batch at the exact minute the dashboard is viewed.
- **Timetable** — lecture-numbered timings, conflict-checked generation
  (teacher availability, lectures/week, room and batch clashes), one-click
  regeneration of a specific batch from its saved prerequisites (the old
  timetable is erased first — generations never stack), and per-slot editing.
- **Edit everywhere** — every record in every module can be edited in place.
- **Attendance reports** — full dated history per student with totals and
  percentage, exportable.
- **Exports** — every module exports to both PDF and .xlsx.
- **Designations** — the owner ("boss") assigns each user a designation
  (Admin / Head / Teacher / Accountant / Clerk / Custom) that controls which
  modules they can open, and can add or revoke individual module privileges
  at any time from Manage Users. Only the boss has access to everything.
