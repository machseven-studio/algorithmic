import os
import json
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Any
import httpx

app = FastAPI(title="Algorithmic API")

DB_PATH = os.path.join(os.path.dirname(__file__), "algorithmic.db")

# How long a login session stays valid before the user has to log in again.
SESSION_LIFETIME_DAYS = 30

# ---- very simple in-memory login rate limiting ----
# NOTE: this resets if the server restarts/redeploys. Fine for an MVP; if you
# outgrow it, move this to a small Redis instance instead of a Python dict.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
_failed_logins: dict[str, dict[str, Any]] = {}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(conn, table: str, column: str, ddl: str):
    """Lightweight migration helper: adds a column to an existing table if it
    isn't there yet, so upgrading this file doesn't break a database that was
    created by an older version of it."""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS institutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institute_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            institute_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
    """)
    # workspace holds every piece of saved data for an institute: roster,
    # seating chart, faculty list, timetable, exam slots, duty roster.
    # Storing these as JSON blobs keeps this in step with how the frontend
    # already models the data, rather than inventing a dozen tiny tables.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workspace (
            institute_id INTEGER PRIMARY KEY,
            roster TEXT,
            rows INTEGER,
            cols INTEGER,
            seat_grid TEXT,
            teachers TEXT,
            tt_days INTEGER,
            tt_periods INTEGER,
            schedule TEXT,
            exam_slots TEXT,
            duty_roster TEXT,
            updated_at TEXT
        )
    """)
    _add_column_if_missing(conn, "sessions", "expires_at", "expires_at TEXT")
    conn.commit()
    conn.close()


init_db()


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


class SignupRequest(BaseModel):
    institute_name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _create_session(conn, institute_id: int) -> str:
    token = secrets.token_hex(32)
    expires_at = (datetime.utcnow() + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO sessions (token, institute_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, institute_id, datetime.utcnow().isoformat(), expires_at),
    )
    return token


def _ensure_workspace_row(conn, institute_id: int):
    row = conn.execute("SELECT institute_id FROM workspace WHERE institute_id = ?", (institute_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO workspace (institute_id, roster, rows, cols, seat_grid, teachers, tt_days, tt_periods, schedule, exam_slots, duty_roster, updated_at) "
            "VALUES (?, '[]', 6, 6, NULL, '[]', 5, 6, NULL, '[]', NULL, ?)",
            (institute_id, datetime.utcnow().isoformat()),
        )


@app.post("/api/signup")
def signup(req: SignupRequest):
    if not req.institute_name.strip() or not req.email.strip() or len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Institute name, email, and a password of at least 6 characters are required.")
    conn = get_db()
    existing = conn.execute("SELECT id FROM institutes WHERE email = ?", (req.email.lower().strip(),)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    salt = secrets.token_hex(16)
    pw_hash = hash_password(req.password, salt)
    cur = conn.execute(
        "INSERT INTO institutes (institute_name, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
        (req.institute_name.strip(), req.email.lower().strip(), pw_hash, salt, datetime.utcnow().isoformat()),
    )
    institute_id = cur.lastrowid
    _ensure_workspace_row(conn, institute_id)
    token = _create_session(conn, institute_id)
    conn.commit()
    conn.close()
    return {"token": token, "institute_name": req.institute_name.strip()}


def _check_rate_limit(email: str):
    entry = _failed_logins.get(email)
    if not entry:
        return
    locked_until = entry.get("locked_until")
    if locked_until and datetime.utcnow() < locked_until:
        wait_minutes = max(1, int((locked_until - datetime.utcnow()).total_seconds() // 60) + 1)
        raise HTTPException(status_code=429, detail=f"Too many failed attempts. Try again in about {wait_minutes} minute(s).")


def _record_failed_login(email: str):
    entry = _failed_logins.setdefault(email, {"count": 0, "locked_until": None})
    entry["count"] += 1
    if entry["count"] >= MAX_FAILED_ATTEMPTS:
        entry["locked_until"] = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        entry["count"] = 0


def _clear_failed_logins(email: str):
    _failed_logins.pop(email, None)


@app.post("/api/login")
def login(req: LoginRequest):
    email = req.email.lower().strip()
    _check_rate_limit(email)
    conn = get_db()
    row = conn.execute("SELECT * FROM institutes WHERE email = ?", (email,)).fetchone()
    if not row or hash_password(req.password, row["salt"]) != row["password_hash"]:
        conn.close()
        _record_failed_login(email)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    _clear_failed_logins(email)
    _ensure_workspace_row(conn, row["id"])
    token = _create_session(conn, row["id"])
    conn.commit()
    conn.close()
    return {"token": token, "institute_name": row["institute_name"]}


def get_current_institute(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in.")
    token = authorization.split(" ", 1)[1]
    conn = get_db()
    row = conn.execute("""
        SELECT institutes.id as id, institutes.institute_name as institute_name, sessions.expires_at as expires_at
        FROM sessions JOIN institutes ON sessions.institute_id = institutes.id
        WHERE sessions.token = ?
    """, (token,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    conn.close()
    return {"id": row["id"], "institute_name": row["institute_name"]}


@app.get("/api/me")
def me(authorization: Optional[str] = Header(None)):
    institute = get_current_institute(authorization)
    return institute


@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    return {"ok": True}


# ---------------- WORKSPACE PERSISTENCE ----------------
# Everything the frontend builds (roster, seating chart, faculty list,
# timetable, exam slots, duty roster) gets saved here so a page refresh, a
# different device, or a server restart doesn't wipe someone's term of work.

def _loads(text: Optional[str], default):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


@app.get("/api/state")
def get_state(authorization: Optional[str] = Header(None)):
    institute = get_current_institute(authorization)
    conn = get_db()
    _ensure_workspace_row(conn, institute["id"])
    conn.commit()
    row = conn.execute("SELECT * FROM workspace WHERE institute_id = ?", (institute["id"],)).fetchone()
    conn.close()
    return {
        "roster": _loads(row["roster"], []),
        "rows": row["rows"] or 6,
        "cols": row["cols"] or 6,
        "seatGrid": _loads(row["seat_grid"], None),
        "teachers": _loads(row["teachers"], []),
        "ttDays": row["tt_days"] or 5,
        "ttPeriods": row["tt_periods"] or 6,
        "schedule": _loads(row["schedule"], None),
        "examSlots": _loads(row["exam_slots"], []),
        "dutyRoster": _loads(row["duty_roster"], None),
    }


class StateUpdate(BaseModel):
    roster: Optional[List[Any]] = None
    rows: Optional[int] = None
    cols: Optional[int] = None
    seatGrid: Optional[Any] = None
    teachers: Optional[List[Any]] = None
    ttDays: Optional[int] = None
    ttPeriods: Optional[int] = None
    schedule: Optional[Any] = None
    examSlots: Optional[List[Any]] = None
    dutyRoster: Optional[Any] = None


@app.put("/api/state")
def put_state(update: StateUpdate, authorization: Optional[str] = Header(None)):
    institute = get_current_institute(authorization)
    conn = get_db()
    _ensure_workspace_row(conn, institute["id"])

    fields = []
    values = []
    mapping = {
        "roster": ("roster", json.dumps(update.roster) if update.roster is not None else None),
        "rows": ("rows", update.rows),
        "cols": ("cols", update.cols),
        "seatGrid": ("seat_grid", json.dumps(update.seatGrid) if update.seatGrid is not None else None),
        "teachers": ("teachers", json.dumps(update.teachers) if update.teachers is not None else None),
        "ttDays": ("tt_days", update.ttDays),
        "ttPeriods": ("tt_periods", update.ttPeriods),
        "schedule": ("schedule", json.dumps(update.schedule) if update.schedule is not None else None),
        "examSlots": ("exam_slots", json.dumps(update.examSlots) if update.examSlots is not None else None),
        "dutyRoster": ("duty_roster", json.dumps(update.dutyRoster) if update.dutyRoster is not None else None),
    }
    incoming = update.dict(exclude_unset=True)
    for key in incoming:
        column, value = mapping[key]
        fields.append(f"{column} = ?")
        values.append(value)

    if fields:
        fields.append("updated_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.append(institute["id"])
        conn.execute(f"UPDATE workspace SET {', '.join(fields)} WHERE institute_id = ?", values)
        conn.commit()
    conn.close()
    return {"ok": True}


# ---------------- AI ASSISTANT ----------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

ASSISTANT_SYSTEM_PROMPT = """You are the in-app assistant for an education institute's operations tool.
You control exactly three things: a classroom seating grid, a weekly class timetable, and an exam duty roster (which staff member supervises which exam slot).
Your only job is to make requested changes to one or more of those, based on the current state given to you.
If the request has nothing to do with seating, timetabling, or exam duty, politely say that's outside what you can do here, and change nothing.
Respond with ONLY a JSON object, no markdown fences, no commentary outside the JSON, in this exact shape:
{"reply": "short plain-language explanation of what you did or why you couldn't", "seatGrid": <updated seat grid array, or null if unchanged>, "schedule": <updated schedule object, or null if unchanged>, "dutyRoster": <updated duty roster object, or null if unchanged>}
Preserve the existing data structure shapes exactly when you modify them - only change the specific cells relevant to the request.
"""


class AssistantRequest(BaseModel):
    message: str
    seatGrid: Optional[Any] = None
    schedule: Optional[Any] = None
    dutyRoster: Optional[Any] = None


@app.post("/api/assistant")
async def assistant(req: AssistantRequest, authorization: Optional[str] = Header(None)):
    get_current_institute(authorization)
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set on the server. Add it in Render's Environment tab.")
    current_state = {"seatGrid": req.seatGrid, "schedule": req.schedule, "dutyRoster": req.dutyRoster}
    user_content = f"Current state:\n{json.dumps(current_state)}\n\nRequest: {req.message}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                params={"key": GEMINI_API_KEY},
                headers={"content-type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": ASSISTANT_SYSTEM_PROMPT}]},
                    "contents": [{"role": "user", "parts": [{"text": user_content}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Gemini: {str(e)}")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Gemini returned {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
        candidates = data.get("candidates")
        if not candidates:
            raise ValueError(f"No candidates in Gemini response: {json.dumps(data)[:300]}")
        text = candidates[0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
        return parsed
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not parse Gemini's response: {str(e)}")


@app.get("/")
def root():
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Algorithmic API is live. Place index.html in a /static folder next to main.py, or visit /docs."}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
