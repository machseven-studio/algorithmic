import os
import json
import sqlite3
import secrets
import hashlib
import shutil
import smtplib
import asyncio
import logging
import threading
import time
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Any
import httpx

app = FastAPI(title="Algorithmic API")

# ---------------- LOGGING & ALERTING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("algorithmic")

# Optional: set this to a Slack/Discord incoming-webhook URL (or anything that
# accepts a JSON POST with a "text" field) to get pinged when something breaks.
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL")


async def send_alert(message: str):
    if not ALERT_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(ALERT_WEBHOOK_URL, json={"text": message})
    except Exception:
        logger.exception("Failed to deliver alert webhook")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    asyncio.create_task(send_alert(f":red_circle: Algorithmic error on {request.method} {request.url.path}: {exc}"))
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on our end. Please try again in a moment."})


# ---------------- DATABASE PATH / PERSISTENCE ----------------
# On Render (or any host with an ephemeral filesystem), a redeploy wipes
# anything not on a mounted persistent disk. Point ALGORITHMIC_DB_PATH at a
# file on that disk (e.g. a Render Disk mounted at /data) in production —
# see README for exact setup steps. Locally this just falls back to a file
# next to this script, same as before.
DB_PATH = os.environ.get("ALGORITHMIC_DB_PATH", os.path.join(os.path.dirname(__file__), "algorithmic.db"))

# ---------------- AUTOMATIC BACKUPS ----------------
BACKUP_DIR = os.environ.get("ALGORITHMIC_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups"))
BACKUP_INTERVAL_SECONDS = int(os.environ.get("ALGORITHMIC_BACKUP_INTERVAL_SECONDS", 6 * 60 * 60))  # every 6h
BACKUP_KEEP = int(os.environ.get("ALGORITHMIC_BACKUP_KEEP", 28))  # ~1 week of history at the default interval


def run_backup_once():
    try:
        if not os.path.exists(DB_PATH):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(BACKUP_DIR, f"algorithmic-{stamp}.db")
        shutil.copy2(DB_PATH, dest)
        logger.info(f"Backed up database to {dest}")
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("algorithmic-") and f.endswith(".db"))
        while len(backups) > BACKUP_KEEP:
            os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
    except Exception:
        logger.exception("Scheduled backup failed")


def _backup_loop():
    # Take one immediately on boot, then on the configured interval.
    while True:
        run_backup_once()
        time.sleep(BACKUP_INTERVAL_SECONDS)


@app.on_event("startup")
def start_backup_thread():
    threading.Thread(target=_backup_loop, daemon=True).start()


# NOTE: this backs up onto the SAME disk as the live database. That protects
# against "oops, corrupted a table" or "bad migration," but not against
# losing the whole disk. Once there's budget for it, ship these backup files
# somewhere off-box too (S3, Backblaze, etc).


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
            invigilators TEXT,
            attendance TEXT,
            updated_at TEXT
        )
    """)
    # Staff logins: additional accounts under the same institute, separate
    # from the original owner account created at signup. Same workspace,
    # separate credentials, so different clerks don't have to share a login.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institute_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            institute_id INTEGER NOT NULL,
            account_type TEXT NOT NULL,
            staff_id INTEGER,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0
        )
    """)
    _add_column_if_missing(conn, "sessions", "expires_at", "expires_at TEXT")
    _add_column_if_missing(conn, "sessions", "account_type", "account_type TEXT DEFAULT 'owner'")
    _add_column_if_missing(conn, "sessions", "staff_id", "staff_id INTEGER")
    _add_column_if_missing(conn, "workspace", "invigilators", "invigilators TEXT")
    _add_column_if_missing(conn, "workspace", "attendance", "attendance TEXT")
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


SESSION_LIFETIME_DAYS = 30


def _create_session(conn, institute_id: int, account_type: str = "owner", staff_id: Optional[int] = None) -> str:
    token = secrets.token_hex(32)
    expires_at = (datetime.utcnow() + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO sessions (token, institute_id, created_at, expires_at, account_type, staff_id) VALUES (?, ?, ?, ?, ?, ?)",
        (token, institute_id, datetime.utcnow().isoformat(), expires_at, account_type, staff_id),
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
    email = req.email.lower().strip()
    existing_owner = conn.execute("SELECT id FROM institutes WHERE email = ?", (email,)).fetchone()
    existing_staff = conn.execute("SELECT id FROM staff_users WHERE email = ?", (email,)).fetchone()
    if existing_owner or existing_staff:
        conn.close()
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    salt = secrets.token_hex(16)
    pw_hash = hash_password(req.password, salt)
    cur = conn.execute(
        "INSERT INTO institutes (institute_name, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
        (req.institute_name.strip(), email, pw_hash, salt, datetime.utcnow().isoformat()),
    )
    institute_id = cur.lastrowid
    _ensure_workspace_row(conn, institute_id)
    token = _create_session(conn, institute_id, "owner")
    conn.commit()
    conn.close()
    return {"token": token, "institute_name": req.institute_name.strip(), "role": "owner"}


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
    if row and hash_password(req.password, row["salt"]) == row["password_hash"]:
        _clear_failed_logins(email)
        _ensure_workspace_row(conn, row["id"])
        token = _create_session(conn, row["id"], "owner")
        conn.commit()
        conn.close()
        return {"token": token, "institute_name": row["institute_name"], "role": "owner"}

    staff = conn.execute("SELECT * FROM staff_users WHERE email = ?", (email,)).fetchone()
    if staff and hash_password(req.password, staff["salt"]) == staff["password_hash"]:
        inst = conn.execute("SELECT institute_name FROM institutes WHERE id = ?", (staff["institute_id"],)).fetchone()
        _clear_failed_logins(email)
        _ensure_workspace_row(conn, staff["institute_id"])
        token = _create_session(conn, staff["institute_id"], "staff", staff["id"])
        conn.commit()
        conn.close()
        return {"token": token, "institute_name": inst["institute_name"] if inst else "", "role": "staff", "staff_name": staff["name"]}

    conn.close()
    _record_failed_login(email)
    raise HTTPException(status_code=401, detail="Incorrect email or password.")


def get_current_institute(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in.")
    token = authorization.split(" ", 1)[1]
    conn = get_db()
    row = conn.execute("""
        SELECT institutes.id as id, institutes.institute_name as institute_name,
               sessions.expires_at as expires_at, sessions.account_type as account_type,
               sessions.staff_id as staff_id
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
    return {
        "id": row["id"],
        "institute_name": row["institute_name"],
        "role": row["account_type"] or "owner",
        "staff_id": row["staff_id"],
    }


def _require_owner(institute: dict):
    if institute.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the institute owner can manage staff logins.")


@app.get("/api/me")
def me(authorization: Optional[str] = Header(None)):
    return get_current_institute(authorization)


@app.post("/api/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    return {"ok": True}


# ---------------- STAFF LOGINS ----------------
class StaffCreateRequest(BaseModel):
    name: str
    email: str
    password: str


@app.get("/api/staff")
def list_staff(authorization: Optional[str] = Header(None)):
    institute = get_current_institute(authorization)
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, email, created_at FROM staff_users WHERE institute_id = ? ORDER BY created_at",
        (institute["id"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/staff")
def add_staff(req: StaffCreateRequest, authorization: Optional[str] = Header(None)):
    institute = get_current_institute(authorization)
    _require_owner(institute)
    if not req.name.strip() or not req.email.strip() or len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Name, email, and a password of at least 6 characters are required.")
    email = req.email.lower().strip()
    conn = get_db()
    existing_owner = conn.execute("SELECT id FROM institutes WHERE email = ?", (email,)).fetchone()
    existing_staff = conn.execute("SELECT id FROM staff_users WHERE email = ?", (email,)).fetchone()
    if existing_owner or existing_staff:
        conn.close()
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    salt = secrets.token_hex(16)
    pw_hash = hash_password(req.password, salt)
    conn.execute(
        "INSERT INTO staff_users (institute_id, name, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (institute["id"], req.name.strip(), email, pw_hash, salt, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/staff/{staff_id}")
def remove_staff(staff_id: int, authorization: Optional[str] = Header(None)):
    institute = get_current_institute(authorization)
    _require_owner(institute)
    conn = get_db()
    conn.execute("DELETE FROM staff_users WHERE id = ? AND institute_id = ?", (staff_id, institute["id"]))
    conn.execute("DELETE FROM sessions WHERE staff_id = ?", (staff_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------- PASSWORD RESET ----------------
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
RESET_FROM_EMAIL = os.environ.get("RESET_FROM_EMAIL", SMTP_USER or "no-reply@algorithmic.app")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")


def _send_reset_email_sync(to_email: str, reset_link: str):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        # SMTP isn't configured yet — log the link so you can still test/use
        # this flow manually. Set SMTP_HOST/SMTP_USER/SMTP_PASS on Render to
        # actually deliver these for real.
        logger.info(f"[password reset] SMTP not configured — link for {to_email}: {reset_link}")
        return
    msg = MIMEText(
        f"Reset your Algorithmic password using the link below (valid for 1 hour):\n\n{reset_link}\n\n"
        "If you didn't request this, you can ignore this email."
    )
    msg["Subject"] = "Reset your Algorithmic password"
    msg["From"] = RESET_FROM_EMAIL
    msg["To"] = to_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(RESET_FROM_EMAIL, [to_email], msg.as_string())
    except Exception:
        logger.exception(f"Failed to send reset email to {to_email}")


class ForgotPasswordRequest(BaseModel):
    email: str


@app.post("/api/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    email = req.email.lower().strip()
    conn = get_db()
    owner = conn.execute("SELECT id FROM institutes WHERE email = ?", (email,)).fetchone()
    staff = None if owner else conn.execute("SELECT id, institute_id FROM staff_users WHERE email = ?", (email,)).fetchone()

    if owner or staff:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        if owner:
            conn.execute(
                "INSERT INTO password_resets (token, institute_id, account_type, staff_id, created_at, expires_at, used) VALUES (?, ?, 'owner', NULL, ?, ?, 0)",
                (token, owner["id"], datetime.utcnow().isoformat(), expires_at),
            )
        else:
            conn.execute(
                "INSERT INTO password_resets (token, institute_id, account_type, staff_id, created_at, expires_at, used) VALUES (?, ?, 'staff', ?, ?, ?, 0)",
                (token, staff["institute_id"], staff["id"], datetime.utcnow().isoformat(), expires_at),
            )
        conn.commit()
        reset_link = f"{APP_BASE_URL}/?reset_token={token}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_reset_email_sync, email, reset_link)

    conn.close()
    # Same response either way — never reveal whether an email is registered.
    return {"message": "If that email is registered, a password reset link has been sent."}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@app.post("/api/reset-password")
def reset_password(req: ResetPasswordRequest):
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    conn = get_db()
    row = conn.execute("SELECT * FROM password_resets WHERE token = ?", (req.token,)).fetchone()
    if not row or row["used"] or datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        conn.close()
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")

    salt = secrets.token_hex(16)
    pw_hash = hash_password(req.new_password, salt)
    if row["account_type"] == "owner":
        conn.execute("UPDATE institutes SET password_hash = ?, salt = ? WHERE id = ?", (pw_hash, salt, row["institute_id"]))
    else:
        conn.execute("UPDATE staff_users SET password_hash = ?, salt = ? WHERE id = ?", (pw_hash, salt, row["staff_id"]))
    conn.execute("UPDATE password_resets SET used = 1 WHERE token = ?", (req.token,))
    # Invalidate every existing session for this institute as a safety measure.
    conn.execute("DELETE FROM sessions WHERE institute_id = ?", (row["institute_id"],))
    conn.commit()
    conn.close()
    return {"message": "Password updated. You can now log in with your new password."}


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
        "invigilators": _loads(row["invigilators"], []),
        "attendance": _loads(row["attendance"], []),
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
    invigilators: Optional[List[Any]] = None
    attendance: Optional[List[Any]] = None


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
        "invigilators": ("invigilators", json.dumps(update.invigilators) if update.invigilators is not None else None),
        "attendance": ("attendance", json.dumps(update.attendance) if update.attendance is not None else None),
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
You control exactly three things: a classroom seating grid, a weekly class timetable, and an invigilator exam duty list (which clerk/invigilator supervises which exam slot — invigilation here is done by hired clerks, not teaching faculty).
Your only job is to make requested changes to one or more of those, based on the current state given to you.
If the request has nothing to do with seating, timetabling, or invigilator exam duty, politely say that's outside what you can do here, and change nothing.
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


@app.get("/terms")
def terms_page():
    p = os.path.join(os.path.dirname(__file__), "static", "terms.html")
    if os.path.exists(p):
        return FileResponse(p)
    raise HTTPException(status_code=404, detail="Not found.")


@app.get("/privacy")
def privacy_page():
    p = os.path.join(os.path.dirname(__file__), "static", "privacy.html")
    if os.path.exists(p):
        return FileResponse(p)
    raise HTTPException(status_code=404, detail="Not found.")


@app.get("/health")
def health_check():
    return {"status": "healthy"}
