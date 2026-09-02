# ALGORITHMIC

Institutional operations dashboard with PostgreSQL-backed multi-tenant data,
constraint-safe timetable generation, attendance history, analytics, role-based
access, and PDF/XLSX exports.

## Run locally

1. Create a PostgreSQL database and set the connection string:

   ```bash
   export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/algorithmic
   ```

2. Install dependencies and start the API:

   ```bash
   python -m pip install -r requirements.txt
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

The schema is created idempotently on application startup. **SQLite is not
supported.** The browser UI is served from `index.html` at `/`.

## Production configuration

Use a managed PostgreSQL connection string in `DATABASE_URL` (or
`POSTGRES_URL`) and run the same Uvicorn command behind the platform's HTTP
proxy. The service binds to `0.0.0.0` so hosted previews and production
proxies can reach it.

## Main API capabilities

- `POST /api/timetable/generate` atomically replaces a batch timetable while a
  PostgreSQL advisory lock prevents concurrent generations from stacking.
- `PATCH /api/records/{module}/{record_id}` edits records in every data module.
- `GET /api/attendance/student/{student_id}/history` returns dated attendance.
- `GET /api/dashboard/{branch_id}` returns weekly batch attendance, pending
  fees, and live lectures.
- `GET /api/export/{module}/{branch_id}.pdf` and `.xlsx` download reports.
- `/api/users` is boss-only; designation presets and per-module read/edit
  privileges are server-enforced.
