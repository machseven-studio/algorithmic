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
- **Environment variable:** `GEMINI_API_KEY` — required for the Assistant module only.

> Without a persistent disk on Render, the SQLite database is rebuilt on each deploy; add a
> Render Disk mounted at the project directory (or swap SQLite for Postgres) for durable data.

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
