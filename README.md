[README (1).md](https://github.com/user-attachments/files/31614432/README.1.md)
# Algorithmic — Institute Operations

A full-stack operations tool for education institutes. One login per institute, with workspace
data persisted server-side (SQLite) so work survives refreshes, devices, and restarts.

## Modules

| # | Module | What it does |
|---|--------|--------------|
| 01 | **Seating** | Builds a classroom seating chart from a student list (`.xlsx`/`.csv` or manual entry) so no two same-batch students sit adjacent. |
| 02 | **Timetable** | Schedules the week from a faculty list (subject, batch, lectures/week, unavailability) with zero teacher double-booking. |
| 03 | **Invigilator Exam Duty** | Assigns exam supervision to hired invigilators/clerks (never teaching faculty), spread fairly across the list. |
| 04 | **Attendance Report** | Logs absentees with date + reason, tracks repeat-absence patterns. |
| 05 | **Assistant** | Scoped Gemini assistant for out-of-script edits to the seating grid, timetable, and invigilator duty. |

Every report module (01–04) exports a formatted **PDF** (jsPDF) — institute header, generation date, ready to circulate.

## Stack

- **Backend:** FastAPI + SQLite (`main.py`) — token auth with rate-limited login, debounced workspace persistence, Gemini proxy (key stays server-side).
- **Frontend:** single-file vanilla app served from `static/index.html`.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# open http://127.0.0.1:8000
```

## Deploy (Render)

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`

### Required: attach a persistent disk (do this before onboarding any real customer)

Without this, **every redeploy wipes the database.** In the Render dashboard, on this service:

1. Go to **Disks** → **Add Disk**. Mount path: `/data`. 1 GB is plenty to start.
2. Set environment variables:
   - `ALGORITHMIC_DB_PATH=/data/algorithmic.db`
   - `ALGORITHMIC_BACKUP_DIR=/data/backups`
3. Redeploy. `main.py` will create the DB at that path on first boot, and back it up automatically every 6 hours (keeps the last 28 snapshots — see `ALGORITHMIC_BACKUP_INTERVAL_SECONDS` / `ALGORITHMIC_BACKUP_KEEP` to tune). These backups still live on the same disk, so they don't protect against losing the whole disk — once there's budget, ship them off-box too (S3, Backblaze, etc).

### Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | For the Assistant module | Google Gemini API key, used server-side only |
| `ALGORITHMIC_DB_PATH` | Yes, in production | Path to the SQLite file — point this at the mounted disk |
| `ALGORITHMIC_BACKUP_DIR` | Recommended | Where periodic DB backups are written |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | For password-reset emails to actually send | Standard SMTP creds (e.g. a Gmail app password, or a transactional email provider). Without these, reset links are only written to the server logs — fine for testing, not for real customers. |
| `RESET_FROM_EMAIL` | Optional | From-address on reset emails; defaults to `SMTP_USER` |
| `APP_BASE_URL` | Yes, in production | Public URL of the deployed app, used to build the link inside reset emails (e.g. `https://your-app.onrender.com`) |
| `ALERT_WEBHOOK_URL` | Recommended | A Slack/Discord incoming-webhook URL. Any unhandled server error gets POSTed here so you find out before a customer emails you. |

### Staff logins

Institutes can now create additional logins under their account (Sidebar → **Manage staff**, owner-only) so different clerks don't have to share one password. All staff logins share the same workspace data as the owner account.

### Password reset

`/api/forgot-password` and `/api/reset-password` back the "Forgot password?" link on the login screen. Reset links expire after 1 hour and are single-use; resetting a password also logs out every existing session for that institute as a precaution.

## Project layout

```
.
├── main.py              # FastAPI backend (auth, state, assistant proxy)
├── requirements.txt
├── .python-version      # 3.12
├── static/
│   └── index.html       # the whole frontend
└── algorithmic.db       # created on first run (git-ignored)
```
