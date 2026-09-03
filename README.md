[README (4).md](https://github.com/user-attachments/files/31792144/README.4.md)
# Algorithmic — Multi-Branch Institutional Operations SaaS

Push-ready FastAPI + PostgreSQL + Tailwind/HTML build.

## Included
- PostgreSQL-backed multi-tenant data model (`tenant_id` boundary via branches).
- `🏢 Centralized HQ (Read-Only)` synthetic scope (`branch_id=0`) for cross-branch reads.
- Branch-scoped authorization on every data route.
- Server-side PostgreSQL sorting, filtering and pagination for generic records.
- Timetable generation with teacher interval-overlap checks and classroom-capacity validation.
- Exam seating generation constrained by registered room capacity.
- Analytics API + sidebar module with attendance trends, revenue and performance metrics.
- Audit trail for application DB mutations with before/after payloads.
- Mandatory UTR / Reference No. when manually marking a fee paid.
- Render blueprint and PostgreSQL dependency configuration.

## Render
1. Create/push this directory as the repository root.
2. Use `render.yaml` to create the web service and PostgreSQL database, or attach an existing Render PostgreSQL instance.
3. Ensure `DATABASE_URL` is present on the web service.
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`.

`index.html` is loaded by `main.py`, so the frontend and API cannot silently drift into separate copies.
