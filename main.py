# main.py
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Depends
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

app = FastAPI(title="ALGORITHMIC", version="4.0.0")

DB_FILE = "algorithmic_enterprise.db"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

SESSION_LIFETIME_DAYS = 7
PBKDF2_ITERATIONS = 200_000
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

VALID_MODULES = ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'invigilation', 'fees']


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS institutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institute_name TEXT NOT NULL,
            full_name TEXT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            institute_id INTEGER NOT NULL,
            staff_user_id INTEGER,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(institute_id) REFERENCES institutes(id)
        )
    """)
    # Migration for pre-existing DBs created before staff_user_id existed.
    try:
        cursor.execute("ALTER TABLE sessions ADD COLUMN staff_user_id INTEGER")
    except sqlite3.OperationalError:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institute_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            permission TEXT NOT NULL DEFAULT 'read_only',
            created_at TEXT NOT NULL,
            FOREIGN KEY(institute_id) REFERENCES institutes(id)
        )
    """)
    # branches now belong to a single institute -> real multi-tenancy
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institute_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE(institute_id, name),
            FOREIGN KEY(institute_id) REFERENCES institutes(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            name TEXT,
            email TEXT,
            course TEXT,
            status TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            name TEXT,
            subject TEXT,
            department TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classrooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            room_no TEXT,
            capacity INTEGER,
            building TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS syllabus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            subject TEXT,
            semester TEXT,
            units INTEGER,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            student_name TEXT,
            date TEXT,
            status TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS timetables_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            batch_name TEXT,
            day TEXT,
            time_slot TEXT,
            subject TEXT,
            teacher TEXT,
            room TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invigilation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            teacher_name TEXT,
            exam_date TEXT,
            room TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            student_name TEXT,
            amount_inr REAL,
            status TEXT,
            due_date TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    conn.commit()
    conn.close()


init_db()


# Schema migrations for the redesigned academic modules. Existing legacy
# columns are intentionally retained so old databases remain readable, while
# the public API/UI expose only the new fields.
def migrate_academic_schema():
    conn = get_conn()
    cursor = conn.cursor()
    migrations = {
        "students": [
            ("roll_number", "TEXT"), ("full_name", "TEXT"), ("batch", "TEXT"),
            ("parent_contact", "TEXT")
        ],
        "teachers": [("full_name", "TEXT"), ("contact_number", "TEXT")],
        "syllabus": [
            ("topic", "TEXT"), ("teacher_name", "TEXT"),
            ("number_of_lectures", "INTEGER"), ("lecture_date", "TEXT")
        ],
        "attendance": [("student_id", "INTEGER"), ("batch", "TEXT")]
    }
    for table, cols in migrations.items():
        existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, typ in cols:
            if name not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.commit()
    conn.close()

migrate_academic_schema()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()


def create_session(institute_id: int, staff_user_id: int = None) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (token, institute_id, staff_user_id, expires_at) VALUES (?, ?, ?, ?)",
        (token, institute_id, staff_user_id, expires_at),
    )
    conn.commit()
    conn.close()
    return token


class CurrentInstitute(BaseModel):
    id: int  # institute_id - used for all data scoping, whether owner or staff
    institute_name: str
    full_name: str
    email: str
    is_owner: bool
    permission: str  # 'owner' | 'edit' | 'read_only'


def get_current_institute(authorization: str = Header(None)) -> CurrentInstitute:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sessions WHERE token = ?", (token,))
    session = cursor.fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if datetime.fromisoformat(session["expires_at"]) < datetime.utcnow():
        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Session expired, please log in again")

    cursor.execute("SELECT * FROM institutes WHERE id = ?", (session["institute_id"],))
    institute = cursor.fetchone()

    if not institute:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid session")

    if session["staff_user_id"] is not None:
        cursor.execute("SELECT * FROM staff_users WHERE id = ?", (session["staff_user_id"],))
        staff = cursor.fetchone()
        conn.close()
        if not staff:
            raise HTTPException(status_code=401, detail="Invalid session")
        return CurrentInstitute(
            id=institute["id"],
            institute_name=institute["institute_name"],
            full_name=staff["full_name"],
            email=staff["email"],
            is_owner=False,
            permission=staff["permission"],
        )

    conn.close()
    return CurrentInstitute(
        id=institute["id"],
        institute_name=institute["institute_name"],
        full_name=institute["full_name"] or "",
        email=institute["email"],
        is_owner=True,
        permission="owner",
    )


def require_write_access(institute: CurrentInstitute = Depends(get_current_institute)) -> CurrentInstitute:
    """Blocks any mutating request from a staff login flagged read-only."""
    if institute.permission == "read_only":
        raise HTTPException(status_code=403, detail="Your account has read-only access")
    return institute


def require_owner(institute: CurrentInstitute = Depends(get_current_institute)) -> CurrentInstitute:
    """Manage Users, and other owner-exclusive actions, check this."""
    if not institute.is_owner:
        raise HTTPException(status_code=403, detail="Only the institute owner can do this")
    return institute


def verify_branch_ownership(branch_id: int, institute_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM branches WHERE id = ? AND institute_id = ?", (branch_id, institute_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Branch not found")


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    institute_name: str
    full_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/api/auth/signup")
def signup(req: SignupRequest):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    salt = secrets.token_hex(16)
    password_hash = hash_password(req.password, salt)

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO institutes (institute_name, full_name, email, password_hash, password_salt, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (req.institute_name, req.full_name, req.email.lower(), password_hash, salt, datetime.utcnow().isoformat()),
        )
        institute_id = cursor.lastrowid
        # every new institute gets one starter branch
        cursor.execute(
            "INSERT INTO branches (institute_id, name) VALUES (?, ?)",
            (institute_id, "Main Campus"),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    conn.close()

    token = create_session(institute_id)
    return {
        "token": token,
        "institute_name": req.institute_name,
        "full_name": req.full_name,
        "is_owner": True,
        "permission": "owner",
    }


@app.post("/api/auth/login")
def login(req: LoginRequest):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM institutes WHERE email = ?", (req.email.lower(),))
    institute = cursor.fetchone()

    # Deliberately same error for "no such email" and "wrong password" so
    # attackers can't use this endpoint to find out which emails are registered.
    invalid = HTTPException(status_code=401, detail="Invalid email or password")

    if institute:
        computed_hash = hash_password(req.password, institute["password_salt"])
        if secrets.compare_digest(computed_hash, institute["password_hash"]):
            conn.close()
            token = create_session(institute["id"])
            return {
                "token": token,
                "institute_name": institute["institute_name"],
                "full_name": institute["full_name"] or "",
                "is_owner": True,
                "permission": "owner",
            }

    # Not an owner account (or wrong password) - check staff logins.
    cursor.execute("SELECT * FROM staff_users WHERE email = ?", (req.email.lower(),))
    staff = cursor.fetchone()
    if staff:
        computed_hash = hash_password(req.password, staff["password_salt"])
        if secrets.compare_digest(computed_hash, staff["password_hash"]):
            cursor.execute("SELECT * FROM institutes WHERE id = ?", (staff["institute_id"],))
            parent_institute = cursor.fetchone()
            conn.close()
            token = create_session(staff["institute_id"], staff_user_id=staff["id"])
            return {
                "token": token,
                "institute_name": parent_institute["institute_name"] if parent_institute else "",
                "full_name": staff["full_name"],
                "is_owner": False,
                "permission": staff["permission"],
            }

    conn.close()
    raise invalid


@app.get("/api/auth/me")
def whoami(institute: CurrentInstitute = Depends(get_current_institute)):
    return {
        "institute_name": institute.institute_name,
        "full_name": institute.full_name,
        "is_owner": institute.is_owner,
        "permission": institute.permission,
    }


@app.post("/api/auth/logout")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        conn = get_conn()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    return {"status": "logged out"}


# ---------------------------------------------------------------------------
# Institute profile
# ---------------------------------------------------------------------------

class InstituteNameUpdate(BaseModel):
    institute_name: str


@app.patch("/api/institute/name")
def update_institute_name(req: InstituteNameUpdate, institute: CurrentInstitute = Depends(require_write_access)):
    name = req.institute_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Institute name cannot be empty")
    conn = get_conn()
    conn.execute("UPDATE institutes SET institute_name = ? WHERE id = ?", (name, institute.id))
    conn.commit()
    conn.close()
    return {"institute_name": name}


# ---------------------------------------------------------------------------
# Staff users ("Manage Users") - owner-only administration
# ---------------------------------------------------------------------------

class StaffUserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    permission: str  # 'edit' | 'read_only'


class StaffPermissionUpdate(BaseModel):
    permission: str


@app.get("/api/users")
def list_staff_users(institute: CurrentInstitute = Depends(require_owner)):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, full_name, email, permission, created_at FROM staff_users WHERE institute_id = ?",
        (institute.id,),
    )
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return users


@app.post("/api/users")
def add_staff_user(req: StaffUserCreate, institute: CurrentInstitute = Depends(require_owner)):
    if req.permission not in ("edit", "read_only"):
        raise HTTPException(status_code=400, detail="Permission must be 'edit' or 'read_only'")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    salt = secrets.token_hex(16)
    password_hash = hash_password(req.password, salt)

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO staff_users (institute_id, full_name, email, password_hash, password_salt, permission, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (institute.id, req.full_name, req.email.lower(), password_hash, salt, req.permission, datetime.utcnow().isoformat()),
        )
        conn.commit()
        user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="A user with this email already exists")
    conn.close()
    return {"id": user_id, "full_name": req.full_name, "email": req.email.lower(), "permission": req.permission}


def verify_staff_ownership(user_id: int, institute_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM staff_users WHERE id = ? AND institute_id = ?", (user_id, institute_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")


@app.patch("/api/users/{user_id}")
def update_staff_permission(user_id: int, req: StaffPermissionUpdate, institute: CurrentInstitute = Depends(require_owner)):
    if req.permission not in ("edit", "read_only"):
        raise HTTPException(status_code=400, detail="Permission must be 'edit' or 'read_only'")
    verify_staff_ownership(user_id, institute.id)
    conn = get_conn()
    conn.execute("UPDATE staff_users SET permission = ? WHERE id = ?", (req.permission, user_id))
    conn.commit()
    conn.close()
    return {"id": user_id, "permission": req.permission}


@app.delete("/api/users/{user_id}")
def remove_staff_user(user_id: int, institute: CurrentInstitute = Depends(require_owner)):
    verify_staff_ownership(user_id, institute.id)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM staff_users WHERE id = ?", (user_id,))
    cursor.execute("DELETE FROM sessions WHERE staff_user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

class BranchCreate(BaseModel):
    name: str


@app.get("/api/branches")
def get_branches(institute: CurrentInstitute = Depends(get_current_institute)):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM branches WHERE institute_id = ?", (institute.id,))
    branches = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return branches


@app.post("/api/branches")
def add_branch(branch: BranchCreate, institute: CurrentInstitute = Depends(require_write_access)):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO branches (institute_id, name) VALUES (?, ?)",
            (institute.id, branch.name),
        )
        conn.commit()
        branch_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Branch already exists")
    conn.close()
    return {"id": branch_id, "name": branch.name}


# ---------------------------------------------------------------------------
# Generic records (students / teachers / classrooms / syllabus / attendance / invigilation / fees)
# ---------------------------------------------------------------------------

@app.get("/api/records/{module}/{branch_id}")
def get_records(module: str, branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    verify_branch_ownership(branch_id, institute.id)

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # module is validated against VALID_MODULES above, so this is safe from injection
    cursor.execute(f"SELECT * FROM {module} WHERE branch_id = ?", (branch_id,))
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return records


def save_upload(file: UploadFile) -> str:
    """Validates type/size and stores the file under a random name (never the
    original filename) so a crafted filename can't be used to write outside
    the uploads directory."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        )
    contents = file.file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    filename = f"{secrets.token_hex(12)}{ext}"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as buffer:
        buffer.write(contents)
    return filename


@app.post("/api/records/{module}")
async def add_record(
    module: str,
    branch_id: int = Form(...),
    data_json: str = Form(...),
    file: UploadFile = File(None),
    institute: CurrentInstitute = Depends(require_write_access),
):
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    verify_branch_ownership(branch_id, institute.id)

    data = json.loads(data_json)
    doc_filename = save_upload(file) if file else None

    conn = get_conn()
    cursor = conn.cursor()

    if module == 'students':
        cursor.execute("INSERT INTO students (branch_id, name, email, course, status, document) VALUES (?, ?, ?, ?, ?, ?)",
                       (branch_id, data.get('name'), data.get('email'), data.get('course'), data.get('status', 'Active'), doc_filename))
    elif module == 'teachers':
        cursor.execute("INSERT INTO teachers (branch_id, name, subject, department, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('name'), data.get('subject'), data.get('department'), doc_filename))
    elif module == 'classrooms':
        cursor.execute("INSERT INTO classrooms (branch_id, room_no, capacity, building, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('room_no'), data.get('capacity'), data.get('building'), doc_filename))
    elif module == 'syllabus':
        cursor.execute("INSERT INTO syllabus (branch_id, subject, semester, units, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('subject'), data.get('semester'), data.get('units'), doc_filename))
    elif module == 'attendance':
        cursor.execute("INSERT INTO attendance (branch_id, student_name, date, status, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('student_name'), data.get('date'), data.get('status'), doc_filename))
    elif module == 'invigilation':
        cursor.execute("INSERT INTO invigilation (branch_id, teacher_name, exam_date, room, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('teacher_name'), data.get('exam_date'), data.get('room'), doc_filename))
    elif module == 'fees':
        cursor.execute("INSERT INTO fees (branch_id, student_name, amount_inr, status, due_date, document) VALUES (?, ?, ?, ?, ?, ?)",
                       (branch_id, data.get('student_name'), data.get('amount_inr'), data.get('status'), data.get('due_date'), doc_filename))

    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return {"id": record_id, "status": "success"}


@app.delete("/api/records/{module}/{record_id}")
def delete_record(module: str, record_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")

    conn = get_conn()
    cursor = conn.cursor()
    # Confirm the record belongs to a branch owned by this institute before deleting.
    cursor.execute(
        f"""SELECT {module}.id FROM {module}
            JOIN branches ON branches.id = {module}.branch_id
            WHERE {module}.id = ? AND branches.institute_id = ?""",
        (record_id, institute.id),
    )
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Record not found")

    cursor.execute(f"DELETE FROM {module} WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


# Column layout expected in a bulk-import CSV for each module (document/email
# intentionally excluded - those are handled per-record, not in bulk).
BULK_IMPORT_COLUMNS = {
    "students": ["name", "batch", "roll_number", "parent_contact"],
    "teachers": ["name", "subject", "contact_number"],
    "classrooms": ["room_no", "capacity"],
    "syllabus": ["subject", "topic", "teacher_name", "number_of_lectures", "date"],
    "attendance": ["student_name", "batch", "date", "status"],
    "invigilation": ["teacher_name", "exam_date", "room"],
    "fees": ["student_name", "amount_inr", "status", "due_date"],
}


@app.post("/api/records/{module}/bulk")
async def bulk_import_records(
    module: str,
    branch_id: int = Form(...),
    file: UploadFile = File(...),
    institute: CurrentInstitute = Depends(require_write_access),
):
    """Lets a user drop in a CSV of many rows at once, instead of typing each
    record in individually through the Add Record form."""
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    verify_branch_ownership(branch_id, institute.id)

    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file")

    import csv
    import io

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Could not read file - please save it as UTF-8 CSV")

    reader = csv.DictReader(io.StringIO(text))
    expected_cols = BULK_IMPORT_COLUMNS[module]
    if not reader.fieldnames or not set(expected_cols).issubset(set(c.strip() for c in reader.fieldnames)):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have these column headers: {', '.join(expected_cols)}",
        )

    conn = get_conn()
    cursor = conn.cursor()
    inserted = 0
    for row in reader:
        row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        if not any(row.values()):
            continue  # skip blank rows

        if module == 'students':
            cursor.execute(
                "INSERT INTO students (branch_id, name, roll_number, full_name, batch, parent_contact) VALUES (?, ?, ?, ?, ?, ?)",
                (branch_id, row.get('name'), row.get('roll_number'), row.get('name'), row.get('batch'), row.get('parent_contact'))
            )
        elif module == 'teachers':
            cursor.execute(
                "INSERT INTO teachers (branch_id, name, full_name, subject, contact_number) VALUES (?, ?, ?, ?, ?)",
                (branch_id, row.get('name'), row.get('name'), row.get('subject'), row.get('contact_number'))
            )
        elif module == 'classrooms':
            cursor.execute("INSERT INTO classrooms (branch_id, room_no, capacity, building, document) VALUES (?, ?, ?, ?, ?)",
                           (branch_id, row.get('room_no'), row.get('capacity'), None, None))
        elif module == 'syllabus':
            cursor.execute(
                "INSERT INTO syllabus (branch_id, subject, topic, teacher_name, number_of_lectures, lecture_date) VALUES (?, ?, ?, ?, ?, ?)",
                (branch_id, row.get('subject'), row.get('topic'), row.get('teacher_name'), row.get('number_of_lectures'), row.get('date'))
            )
        elif module == 'attendance':
            cursor.execute("INSERT INTO attendance (branch_id, student_name, batch, date, status) VALUES (?, ?, ?, ?, ?)",
                           (branch_id, row.get('student_name'), row.get('batch'), row.get('date'), row.get('status')))
        elif module == 'invigilation':
            cursor.execute("INSERT INTO invigilation (branch_id, teacher_name, exam_date, room, document) VALUES (?, ?, ?, ?, ?)",
                           (branch_id, row.get('teacher_name'), row.get('exam_date'), row.get('room'), None))
        elif module == 'fees':
            cursor.execute("INSERT INTO fees (branch_id, student_name, amount_inr, status, due_date, document) VALUES (?, ?, ?, ?, ?, ?)",
                           (branch_id, row.get('student_name'), row.get('amount_inr'), row.get('status'), row.get('due_date'), None))
        inserted += 1

    conn.commit()
    conn.close()
    if inserted == 0:
        raise HTTPException(status_code=400, detail="No valid rows found in that file")
    return {"status": "success", "inserted": inserted}


# ---------------------------------------------------------------------------
# Redesigned academic module APIs
# ---------------------------------------------------------------------------

class StudentCreate(BaseModel):
    branch_id: int
    name: str
    batch: str
    roll_number: str
    parent_contact: str

class TeacherCreate(BaseModel):
    branch_id: int
    name: str
    subject: str
    contact_number: str

class SyllabusCreate(BaseModel):
    branch_id: int
    subject: str
    topic: str
    teacher_name: str
    number_of_lectures: int
    date: str

class AttendanceMark(BaseModel):
    branch_id: int
    student_id: int
    batch: str
    date: str
    status: str

class ClassroomCreate(BaseModel):
    branch_id: int
    room_name: str
    capacity: int
    rows: int
    columns: int

class InvigilatorCreate(BaseModel):
    branch_id: int
    full_name: str
    contact_number: str

class FeeCreate(BaseModel):
    branch_id: int
    student_id: int
    amount_due: float
    due_date: str


def _student_public(row):
    return {
        "id": row["id"], "name": row["full_name"] or row["name"] or "",
        "batch": row["batch"] or "", "roll_number": row["roll_number"] or "",
        "parent_contact": row["parent_contact"] or ""
    }

@app.get("/api/students")
def api_students(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT id, name, full_name, batch, roll_number, parent_contact
                         FROM students WHERE branch_id=?
                         ORDER BY LOWER(COALESCE(batch,'')), LOWER(COALESCE(full_name,name,''))""", (branch_id,)).fetchall()
    conn.close(); return [_student_public(r) for r in rows]

@app.post("/api/students")
def api_create_student(req: StudentCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO students (branch_id,name,full_name,batch,roll_number,parent_contact) VALUES (?,?,?,?,?,?)",
                (req.branch_id,req.name,req.name,req.batch,req.roll_number,req.parent_contact))
    conn.commit(); rid=cur.lastrowid; conn.close(); return {"id":rid,"status":"success"}

@app.get("/api/classrooms")
def api_classrooms(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("SELECT id, room_no AS room_name, capacity, building, document FROM classrooms WHERE branch_id=? ORDER BY id",(branch_id,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/api/classrooms")
def api_create_classroom(req: ClassroomCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO classrooms (branch_id,room_no,capacity,building,document) VALUES (?,?,?,?,NULL)",(req.branch_id,req.room_name,req.capacity,f"{req.rows} x {req.columns}"))
    conn.commit(); rid=cur.lastrowid; conn.close(); return {"id":rid,"status":"success"}

@app.get("/api/invigilators")
def api_invigilators(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("SELECT id, teacher_name AS full_name, '' AS contact_number, room AS assigned_room FROM invigilation WHERE branch_id=? ORDER BY id",(branch_id,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/api/invigilators")
def api_create_invigilator(req: InvigilatorCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO invigilation (branch_id,teacher_name,exam_date,room,document) VALUES (?,?,?,?,NULL)",(req.branch_id,req.full_name,'',None))
    conn.commit(); rid=cur.lastrowid; conn.close(); return {"id":rid,"status":"success"}

@app.get("/api/fees/defaulters")
def api_fee_defaulters(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT f.id, f.student_name, COALESCE(s.roll_number,'') AS roll_number,
                       f.amount_inr AS amount_due, f.due_date, f.status, f.document
                       FROM fees f LEFT JOIN students s ON s.id=f.student_name
                       WHERE f.branch_id=? ORDER BY f.due_date""",(branch_id,)).fetchall()
    out=[]
    today=datetime.utcnow().date()
    for r in rows:
        try: days=(datetime.fromisoformat(r['due_date']).date()-today).days
        except Exception: days=999
        out.append({**dict(r),"days_remaining":days,"urgent_alert":days<=3})
    conn.close(); return out

@app.post("/api/fees")
def api_create_fee(req: FeeCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT COALESCE(full_name,name), roll_number FROM students WHERE id=? AND branch_id=?",(req.student_id,req.branch_id)); st=cur.fetchone()
    if not st: conn.close(); raise HTTPException(status_code=404,detail="Student not found")
    cur.execute("INSERT INTO fees (branch_id,student_name,amount_inr,status,due_date,document) VALUES (?,?,?,?,?,NULL)",(req.branch_id,st[0],req.amount_due,'Pending',req.due_date))
    conn.commit(); rid=cur.lastrowid; conn.close(); return {"id":rid,"status":"success"}

@app.get("/api/student-batches")
def api_student_batches(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn()
    rows=conn.execute("SELECT DISTINCT batch FROM students WHERE branch_id=? AND TRIM(COALESCE(batch,''))<>'' ORDER BY LOWER(batch)", (branch_id,)).fetchall()
    conn.close(); return [r[0] for r in rows]

@app.get("/api/teachers")
def api_teachers(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("SELECT id, COALESCE(full_name,name) AS full_name, subject, contact_number FROM teachers WHERE branch_id=? ORDER BY LOWER(COALESCE(full_name,name,''))", (branch_id,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/api/teachers")
def api_create_teacher(req: TeacherCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO teachers (branch_id,name,full_name,subject,contact_number) VALUES (?,?,?,?,?)", (req.branch_id,req.name,req.name,req.subject,req.contact_number))
    conn.commit(); rid=cur.lastrowid; conn.close(); return {"id":rid,"status":"success"}

@app.get("/api/syllabus")
def api_syllabus(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT id, subject, topic, teacher_name, number_of_lectures, lecture_date
                         FROM syllabus WHERE branch_id=? ORDER BY lecture_date DESC, LOWER(subject)""", (branch_id,)).fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/api/syllabus")
def api_create_syllabus(req: SyllabusCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    if req.number_of_lectures < 1: raise HTTPException(status_code=400, detail="Number of lectures must be at least 1")
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO syllabus (branch_id,subject,topic,teacher_name,number_of_lectures,lecture_date) VALUES (?,?,?,?,?,?)",
                (req.branch_id,req.subject,req.topic,req.teacher_name,req.number_of_lectures,req.date))
    conn.commit(); rid=cur.lastrowid; conn.close(); return {"id":rid,"status":"success"}

@app.get("/api/attendance/students")
def api_attendance_students(branch_id: int, batch: str, date: str = None, q: str = "", institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn(); conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT s.id, COALESCE(s.full_name,s.name) AS name, s.batch, s.roll_number,
                       COALESCE(a.status,'') AS attendance_status
                       FROM students s LEFT JOIN attendance a
                       ON a.student_id=s.id AND a.branch_id=s.branch_id AND a.date=?
                       WHERE s.branch_id=? AND s.batch=? AND LOWER(COALESCE(s.full_name,s.name,'')) LIKE LOWER(?)
                       ORDER BY LOWER(COALESCE(s.full_name,s.name,''))""", (date or "", branch_id, batch, f"%{q}%" )).fetchall()
    conn.close(); return [dict(r) for r in rows]

@app.post("/api/attendance/mark")
def api_mark_attendance(req: AttendanceMark, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    if req.status not in ("Present", "Absent"): raise HTTPException(status_code=400, detail="Status must be Present or Absent")
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT id FROM students WHERE id=? AND branch_id=? AND batch=?", (req.student_id, req.branch_id, req.batch))
    if not cur.fetchone(): conn.close(); raise HTTPException(status_code=404, detail="Student not found in selected batch")
    cur.execute("SELECT id FROM attendance WHERE branch_id=? AND student_id=? AND date=?", (req.branch_id,req.student_id,req.date))
    existing=cur.fetchone()
    if existing:
        cur.execute("UPDATE attendance SET student_name=(SELECT COALESCE(full_name,name) FROM students WHERE id=?), batch=?, status=?, document=NULL WHERE id=?", (req.student_id,req.batch,req.status,existing[0]))
    else:
        cur.execute("INSERT INTO attendance (branch_id,student_id,student_name,batch,date,status) VALUES (?,?,?,?,?,?)", (req.branch_id,req.student_id,None,req.batch,req.date,req.status))
        cur.execute("UPDATE attendance SET student_name=(SELECT COALESCE(full_name,name) FROM students WHERE id=?) WHERE id=?", (req.student_id,cur.lastrowid))
    conn.commit(); conn.close(); return {"status":"success","attendance_status":req.status}

@app.post("/api/academic/import-pdf")
async def import_academic_pdf(module: str = Form(...), branch_id: int = Form(...), file: UploadFile = File(...), institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(branch_id, institute.id)
    if module not in ("students","teachers","syllabus"): raise HTTPException(status_code=400, detail="PDF import is available for students, teachers and syllabus")
    if not (file.filename or "").lower().endswith(".pdf"): raise HTTPException(status_code=400, detail="Please upload a PDF file")
    raw=await file.read()
    if len(raw)>MAX_UPLOAD_BYTES: raise HTTPException(status_code=400, detail="File too large (max 5 MB)")
    try:
        from pypdf import PdfReader
        import io, csv
        text="\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(raw)).pages)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read this PDF: {exc}")
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    expected=BULK_IMPORT_COLUMNS[module]
    norm={h.lower().replace(" ","_"):h for h in expected}
    header_idx=None
    for i,line in enumerate(lines[:40]):
        parts=[x.strip().lower().replace(" ","_") for x in line.replace("|",",").split(",")]
        if len(set(parts)&set(norm)) >= max(2, len(expected)-1): header_idx=i; break
    if header_idx is None: raise HTTPException(status_code=400, detail=f"PDF must contain a tabular header using: {', '.join(expected)}")
    headers=[x.strip().lower().replace(" ","_") for x in lines[header_idx].replace("|",",").split(",")]
    conn=get_conn(); cur=conn.cursor(); inserted=0
    for line in lines[header_idx+1:]:
        parts=[x.strip() for x in line.replace("|",",").split(",")]
        if len(parts)<len(headers): continue
        row=dict(zip(headers,parts))
        try:
            if module=="students":
                cur.execute("INSERT INTO students (branch_id,name,full_name,batch,roll_number,parent_contact) VALUES (?,?,?,?,?,?)",(branch_id,row.get("name"),row.get("name"),row.get("batch"),row.get("roll_number"),row.get("parent_contact")))
            elif module=="teachers":
                cur.execute("INSERT INTO teachers (branch_id,name,full_name,subject,contact_number) VALUES (?,?,?,?,?)",(branch_id,row.get("name"),row.get("name"),row.get("subject"),row.get("contact_number")))
            else:
                cur.execute("INSERT INTO syllabus (branch_id,subject,topic,teacher_name,number_of_lectures,lecture_date) VALUES (?,?,?,?,?,?)",(branch_id,row.get("subject"),row.get("topic"),row.get("teacher_name"),int(row.get("number_of_lectures",0)),row.get("date")))
            inserted+=1
        except (ValueError, sqlite3.Error):
            continue
    conn.commit(); conn.close()
    if not inserted: raise HTTPException(status_code=400, detail="No readable records were found in the PDF")
    return {"status":"success","inserted":inserted}


# ---------------------------------------------------------------------------
# Timetable generation
# ---------------------------------------------------------------------------

@app.get("/api/timetable/slots/{branch_id}")
def get_timetable_slots(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM timetables_slots WHERE branch_id = ?", (branch_id,))
    slots = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return slots


class TimetableGenerateRequest(BaseModel):
    branch_id: int
    batch_name: str
    teachers_config: list  # [{name, subject, lectures_per_week, unavailable_days: []}]
    timings: list  # ["09:00 AM - 10:00 AM", ...]


@app.post("/api/timetable/generate")
def generate_timetable(req: TimetableGenerateRequest, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)

    conn = get_conn()
    cursor = conn.cursor()

    # Clear old slots for this batch only - other batches' slots stay intact
    # so we can still check teacher/room conflicts against them below.
    cursor.execute("DELETE FROM timetables_slots WHERE branch_id = ? AND batch_name = ?", (req.branch_id, req.batch_name))

    # Real classrooms for this branch, used for room assignment instead of a hardcoded room.
    cursor.execute("SELECT room_no FROM classrooms WHERE branch_id = ? ORDER BY id", (req.branch_id,))
    available_rooms = [row[0] for row in cursor.fetchall() if row[0]]

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    generated_slots = []
    warnings = []

    for t_config in req.teachers_config:
        teacher_name = t_config['name']
        subject = t_config['subject']
        target_lectures = int(t_config['lectures_per_week'])
        unavailable = t_config.get('unavailable_days', [])

        assigned_count = 0
        for day in days:
            if day in unavailable:
                continue
            if assigned_count >= target_lectures:
                break
            for slot_time in req.timings:
                if assigned_count >= target_lectures:
                    break

                # 1. Is this batch already busy at this day/time?
                cursor.execute("""
                    SELECT COUNT(*) FROM timetables_slots
                    WHERE branch_id = ? AND batch_name = ? AND day = ? AND time_slot = ?
                """, (req.branch_id, req.batch_name, day, slot_time))
                if cursor.fetchone()[0] > 0:
                    continue

                # 2. Is this teacher already teaching a DIFFERENT batch at this day/time?
                #    (this is the check the old version never did)
                cursor.execute("""
                    SELECT COUNT(*) FROM timetables_slots
                    WHERE branch_id = ? AND day = ? AND time_slot = ? AND teacher = ?
                """, (req.branch_id, day, slot_time, teacher_name))
                if cursor.fetchone()[0] > 0:
                    continue

                # 3. Find a real, currently-free classroom for this day/time
                room = None
                for candidate_room in available_rooms:
                    cursor.execute("""
                        SELECT COUNT(*) FROM timetables_slots
                        WHERE branch_id = ? AND day = ? AND time_slot = ? AND room = ?
                    """, (req.branch_id, day, slot_time, candidate_room))
                    if cursor.fetchone()[0] == 0:
                        room = candidate_room
                        break
                if room is None:
                    room = "Unassigned (no free classroom)" if available_rooms else "Unassigned (add a classroom)"

                cursor.execute("""
                    INSERT INTO timetables_slots (branch_id, batch_name, day, time_slot, subject, teacher, room)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (req.branch_id, req.batch_name, day, slot_time, subject, teacher_name, room))
                generated_slots.append({"day": day, "time_slot": slot_time, "subject": subject, "teacher": teacher_name, "room": room})
                assigned_count += 1

        if assigned_count < target_lectures:
            warnings.append(
                f"{teacher_name}: only scheduled {assigned_count}/{target_lectures} lectures "
                f"(not enough free day/time slots without a conflict)."
            )

    conn.commit()
    conn.close()
    return {"status": "success", "slots": generated_slots, "warnings": warnings}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content=HTML_CONTENT, status_code=200)


HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALGORITHMIC - Enterprise Institutional Operations</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700;9..144,900&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #030303;
            background-image:
                radial-gradient(rgba(212, 175, 55, 0.06) 1.5px, transparent 1.5px),
                radial-gradient(rgba(212, 175, 55, 0.025) 1.5px, #030303 1.5px),
                url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");
            background-size: 40px 40px, 40px 40px, 120px 120px;
            background-position: 0 0, 20px 20px, 0 0;
            color: #f3f4f6;
            overflow-x: hidden;
            font-weight: 500;
        }

        /* Bold display serif used for headlines - premium, editorial, unmistakably "statement" typography */
        .elegant-font { font-family: 'Fraunces', serif; font-weight: 600; }
        h1, h2, h3 { font-family: 'Fraunces', serif; letter-spacing: -0.01em; }

        .gold-gradient-text {
            background: linear-gradient(135deg, #F4E5A1 0%, #E8C767 20%, #BF953F 45%, #8a6a22 60%, #E8C767 80%, #F4E5A1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            filter: drop-shadow(0 1px 1px rgba(0,0,0,0.4));
        }

        .gold-border { border-color: rgba(212, 175, 55, 0.28); }

        .gold-border-glow:focus, .gold-border-glow:hover {
            border-color: #D4AF37;
            box-shadow: 0 0 12px rgba(212, 175, 55, 0.18);
        }

        .gold-bg { background: linear-gradient(135deg, #EACD6E, #AA771C); }

        .glass-panel {
            background:
                linear-gradient(160deg, rgba(20,17,10,0.55), rgba(8,8,8,0.9)),
                repeating-linear-gradient(115deg, rgba(255,255,255,0.012) 0px, rgba(255,255,255,0.012) 1px, transparent 1px, transparent 3px);
            background-color: rgba(10, 10, 10, 0.92);
            border: 1px solid rgba(212, 175, 55, 0.18);
            box-shadow: 0 12px 32px rgba(0,0,0,0.55);
        }

        .sidebar-item { transition: background-color 0.12s ease, color 0.12s ease, border-color 0.12s ease; letter-spacing: 0.06em; }
        .sidebar-item:hover, .sidebar-item.active {
            background: rgba(212, 175, 55, 0.12);
            color: #E8C767;
            border-left: 3px solid #D4AF37;
            padding-left: 1.75rem;
        }

        /* Snappy, low-cost transitions everywhere - no blur/shadow animation, just color/opacity/transform */
        .fast-transition { transition: background-color 0.1s ease, color 0.1s ease, opacity 0.1s ease, transform 0.1s ease; }
        .fast-transition:active { transform: scale(0.97); }

        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #332d16; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #D4AF37; }

        .auth-error { color: #f87171; font-size: 11px; margin-top: 6px; min-height: 14px; }

        .editable-name { cursor: text; border-bottom: 1px dashed rgba(212,175,55,0.4); }
        .editable-name:hover { border-bottom-color: #D4AF37; }

        .perm-badge { font-size: 9px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; padding: 3px 8px; border-radius: 999px; }
        .perm-edit { background: rgba(212,175,55,0.15); color: #E8C767; border: 1px solid rgba(212,175,55,0.35); }
        .perm-readonly { background: rgba(148,163,184,0.1); color: #9ca3af; border: 1px solid rgba(148,163,184,0.25); }

        .row-delete-btn { color: #6b7280; transition: color 0.1s ease; }
        .row-delete-btn:hover { color: #f87171; }
    </style>
</head>
<body class="min-h-screen flex flex-col">

    <!-- LOGIN / SIGNUP SCREEN OVERLAY -->
    <div id="authOverlay" class="fixed inset-0 z-50 bg-[#050505] flex items-center justify-center p-4">
        <div class="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(212,175,55,0.06)_0,transparent_70%)]"></div>
        <div class="glass-panel w-full max-w-md p-8 rounded-2xl shadow-2xl relative z-10 border gold-border">
            <div class="text-center mb-8">
                <div class="inline-block p-3 rounded-full bg-[#121212] border gold-border mb-4 shadow-lg">
                    <span class="text-2xl font-black gold-gradient-text">⚡</span>
                </div>
                <h1 class="text-2xl font-black gold-gradient-text tracking-wider">ALGORITHMIC</h1>
                <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Enterprise Institutional Portal</p>
            </div>

            <!-- LOGIN FORM -->
            <form id="loginForm" onsubmit="handleLogin(event)" class="space-y-5">
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Email</label>
                    <input type="email" id="loginEmail" required placeholder="admin@institute.edu" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Password</label>
                    <input type="password" id="loginPassword" required placeholder="••••••••••••" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div id="loginError" class="auth-error"></div>
                <button type="submit" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3.5 rounded-xl text-sm fast-transition shadow-lg tracking-wider uppercase">
                    Log In
                </button>
                <p class="text-center text-xs text-gray-500 pt-2">New institute? <a href="#" onclick="showSignup(event)" class="gold-gradient-text font-semibold hover:underline">Create an account</a></p>
            </form>

            <!-- SIGNUP FORM -->
            <form id="signupForm" onsubmit="handleSignup(event)" class="space-y-4 hidden">
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Institute Name</label>
                    <input type="text" id="signupInstitute" required placeholder="Algorithmic Academy of Excellence" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Your Name</label>
                    <input type="text" id="signupName" required placeholder="Samarth Dave" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Email</label>
                    <input type="email" id="signupEmail" required placeholder="admin@institute.edu" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Password (min 8 characters)</label>
                    <input type="password" id="signupPassword" required minlength="8" placeholder="••••••••••••" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div id="signupError" class="auth-error"></div>
                <button type="submit" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3.5 rounded-xl text-sm fast-transition shadow-lg tracking-wider uppercase">
                    Create Account
                </button>
                <p class="text-center text-xs text-gray-500 pt-2">Already registered? <a href="#" onclick="showLogin(event)" class="gold-gradient-text font-semibold hover:underline">Log in</a></p>
            </form>

            <div class="mt-8 pt-6 border-t gold-border text-center text-xs text-gray-500 space-y-1">
                <p class="font-semibold text-gray-400">CREATED BY SAMARTH DAVE</p>
                <p>FOUNDER OF <a href="https://machsevenstudios-website.onrender.com" target="_blank" class="gold-gradient-text hover:underline font-semibold">MACHSEVENSTUDIOS</a></p>
                <p class="text-[10px] text-yellow-600/80 tracking-widest uppercase pt-1">POWERED BY METASYS<sup>®</sup></p>
            </div>
        </div>
    </div>

    <!-- MAIN APP CONTAINER -->
    <div id="appContainer" class="min-h-screen flex flex-col hidden">
        <header class="border-b gold-border bg-[#0a0a0a] px-8 py-4 flex justify-between items-center sticky top-0 z-40">
            <div class="flex items-center space-x-4">
                <div class="group flex items-center space-x-2">
                    <h1 id="headerInstituteName" onclick="openRenameInstituteModal()" title="Click to rename your institute" class="editable-name elegant-font text-3xl font-black gold-gradient-text tracking-tight leading-none">—</h1>
                    <button onclick="openRenameInstituteModal()" title="Rename institute" class="text-gray-600 hover:text-yellow-500 text-sm fast-transition opacity-0 group-hover:opacity-100">✎</button>
                </div>
            </div>
            <div class="flex items-center space-x-6 text-sm">
                <div class="flex items-center space-x-2 bg-[#121212] px-3 py-1.5 rounded-lg border gold-border">
                    <span class="text-xs text-gray-400">Active Branch:</span>
                    <select id="branchSelector" class="bg-transparent text-sm font-semibold gold-gradient-text focus:outline-none cursor-pointer"></select>
                    <button onclick="openAddBranchModal()" class="ml-2 text-xs bg-[#1a1a1a] hover:bg-[#252525] gold-gradient-text border gold-border px-2 py-0.5 rounded fast-transition">+ Branch</button>
                </div>
                <div class="text-xs text-right border-l pl-6 gold-border">
                    <div class="text-gray-400">Logged in as</div>
                    <div id="headerFullName" class="font-bold gold-gradient-text">—</div>
                    <div id="headerPermBadge" class="mt-0.5"></div>
                </div>
                <button onclick="handleLogout()" class="text-xs bg-[#161616] hover:bg-[#222] text-red-400 border border-red-900/40 px-3 py-2 rounded-lg fast-transition">Logout</button>
            </div>
        </header>

        <div class="flex flex-1 overflow-hidden">
            <nav class="w-72 border-r gold-border bg-[#0b0b0b] flex flex-col py-6 space-y-1.5 shrink-0">
                <div class="px-6 pb-1 elegant-font text-lg font-bold gold-gradient-text tracking-wide">ALGORITHMIC</div>
                <div class="px-6 pb-2 text-[11px] font-bold text-gray-500 uppercase tracking-widest">Enterprise Modules</div>
                <button onclick="switchModule('home')" class="sidebar-item active w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>⚡</span><span>Home Dashboard</span></button>
                <button onclick="switchModule('students')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🎓</span><span>Students</span></button>
                <button onclick="switchModule('teachers')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>👨‍🏫</span><span>Teachers</span></button>
                <button onclick="switchModule('classrooms')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🏛️</span><span>Classrooms</span></button>
                <button onclick="switchModule('syllabus')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>📚</span><span>Syllabus</span></button>
                <button onclick="switchModule('attendance')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>📋</span><span>Attendance</span></button>
                <button onclick="switchModule('timetables')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🕒</span><span>Timetable</span></button>
                <button onclick="switchModule('invigilation')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🛡️</span><span>Invigilator Duty</span></button>
                <button onclick="switchModule('fees')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>💳</span><span>Fees (INR ₹)</span></button>
                <button id="navManageUsers" onclick="switchModule('users')" class="sidebar-item hidden w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🔐</span><span>Manage Users</span></button>

                <div class="mt-auto px-6 pt-6 border-t gold-border text-[11px] text-gray-400 space-y-1 bg-[#090909]">
                    <p class="text-gray-300">Founded by <a href="https://machsevenstudios-website.onrender.com" target="_blank" class="gold-gradient-text hover:underline">MachSevenStudios</a></p>
                    <p class="text-[10px] text-yellow-600 font-bold uppercase tracking-widest pt-1">Powered by Metasys<sup>®</sup></p>
                </div>
            </nav>

            <main class="flex-1 p-10 overflow-y-auto bg-[#070707]" id="mainContent"></main>
        </div>
    </div>

    <!-- Rename Institute Modal -->
    <div id="renameInstituteModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-md shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Rename Institute</h3>
                <button onclick="closeRenameInstituteModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <div class="space-y-4">
                <input type="text" id="renameInstituteInput" placeholder="Institute Name" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <div id="renameInstituteError" class="auth-error"></div>
                <button onclick="submitRenameInstitute()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Save Name</button>
            </div>
        </div>
    </div>

    <!-- Add Staff User Modal -->
    <div id="userModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-md shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Add User</h3>
                <button onclick="closeUserModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <div class="space-y-4">
                <input type="text" id="newUserName" placeholder="Full Name" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <input type="email" id="newUserEmail" placeholder="Email Address" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <input type="password" id="newUserPassword" placeholder="Password (min 8 characters)" minlength="8" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Permission</label>
                    <select id="newUserPermission" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                        <option value="edit">Edit Access</option>
                        <option value="read_only">Read Only</option>
                    </select>
                </div>
                <div id="userModalError" class="auth-error"></div>
                <button onclick="submitNewUser()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Create User</button>
            </div>
        </div>
    </div>

    <!-- Bulk Import Modal (the "Add Document" adjacent action) -->
    <div id="bulkImportModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-lg shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 id="bulkImportTitle" class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Bulk Import via Document</h3>
                <button onclick="closeBulkImportModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <div class="space-y-4">
                <p class="text-xs text-gray-400 leading-relaxed">Upload a single <span class="text-yellow-500 font-semibold">.csv</span> file to create many records at once, instead of typing each one in individually. The first row must be a header row with these exact column names:</p>
                <div id="bulkImportColumns" class="text-xs font-mono bg-[#0c0c0c] border gold-border rounded-xl p-3 text-yellow-500"></div>
                <input type="file" id="bulkImportFile" accept=".csv" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-2.5 text-xs text-gray-300 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-[#221c0c] file:text-yellow-500 hover:file:bg-[#332a0f]">
                <div id="bulkImportError" class="auth-error"></div>
                <button onclick="submitBulkImport()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Upload &amp; Create Records</button>
            </div>
        </div>
    </div>

    <!-- Generic Add Record Modal -->
    <div id="recordModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-lg shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 id="modalTitle" class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Add Record</h3>
                <button onclick="closeRecordModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <form id="recordForm" onsubmit="submitRecordForm(event)" class="space-y-4">
                <div id="modalFields" class="space-y-4"></div>
                <p class="text-[11px] text-gray-500">Need to attach a document, or add many records at once? Use the <span class="text-yellow-500 font-semibold">+ Add Document</span> button next to this one instead.</p>
                <div id="recordFormError" class="auth-error"></div>
                <div class="flex justify-end space-x-3 pt-4 border-t gold-border">
                    <button type="button" onclick="closeRecordModal()" class="px-5 py-2.5 text-xs font-bold uppercase bg-gray-900 hover:bg-gray-800 text-gray-300 rounded-xl fast-transition">Cancel</button>
                    <button type="submit" class="px-6 py-2.5 text-xs font-extrabold uppercase gold-bg hover:opacity-95 text-black rounded-xl fast-transition shadow-lg">Save Record</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Add Branch Modal -->
    <div id="branchModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-md shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Add Branch</h3>
                <button onclick="closeAddBranchModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <div class="space-y-4">
                <input type="text" id="newBranchName" placeholder="e.g. North Campus - Pune" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <button onclick="createNewBranch()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Create Branch</button>
            </div>
        </div>
    </div>

    <script>
        let branches = [];
        let currentBranchId = null;
        let currentModule = 'home';
        let authToken = localStorage.getItem('algorithmic_token');
        let isOwner = false;
        let myPermission = 'owner';
        let bulkImportModule = null;

        // ---- Auth ----

        function showSignup(e) { e.preventDefault(); document.getElementById('loginForm').classList.add('hidden'); document.getElementById('signupForm').classList.remove('hidden'); }
        function showLogin(e) { e.preventDefault(); document.getElementById('signupForm').classList.add('hidden'); document.getElementById('loginForm').classList.remove('hidden'); }

        async function handleLogin(e) {
            e.preventDefault();
            const errorEl = document.getElementById('loginError');
            errorEl.textContent = '';
            const email = document.getElementById('loginEmail').value;
            const password = document.getElementById('loginPassword').value;
            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                const data = await res.json();
                if (!res.ok) { errorEl.textContent = data.detail || 'Login failed.'; return; }
                completeAuth(data);
            } catch (err) { errorEl.textContent = 'Network error. Please try again.'; }
        }

        async function handleSignup(e) {
            e.preventDefault();
            const errorEl = document.getElementById('signupError');
            errorEl.textContent = '';
            const institute_name = document.getElementById('signupInstitute').value;
            const full_name = document.getElementById('signupName').value;
            const email = document.getElementById('signupEmail').value;
            const password = document.getElementById('signupPassword').value;
            try {
                const res = await fetch('/api/auth/signup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ institute_name, full_name, email, password })
                });
                const data = await res.json();
                if (!res.ok) { errorEl.textContent = data.detail || 'Signup failed.'; return; }
                completeAuth(data);
            } catch (err) { errorEl.textContent = 'Network error. Please try again.'; }
        }

        function applyIdentity(data) {
            isOwner = !!data.is_owner;
            myPermission = data.permission || 'owner';
            document.getElementById('headerInstituteName').textContent = data.institute_name;
            document.getElementById('headerFullName').textContent = data.full_name || data.institute_name;
            document.getElementById('navManageUsers').classList.toggle('hidden', !isOwner);
            const badge = document.getElementById('headerPermBadge');
            if (isOwner) { badge.innerHTML = ''; }
            else if (myPermission === 'read_only') { badge.innerHTML = '<span class="perm-badge perm-readonly">Read Only</span>'; }
            else { badge.innerHTML = '<span class="perm-badge perm-edit">Edit Access</span>'; }
        }

        function completeAuth(data) {
            authToken = data.token;
            localStorage.setItem('algorithmic_token', authToken);
            applyIdentity(data);
            document.getElementById('authOverlay').classList.add('hidden');
            document.getElementById('appContainer').classList.remove('hidden');
            initApp();
        }

        async function handleLogout() {
            try { await authFetch('/api/auth/logout', { method: 'POST' }); } catch (e) {}
            localStorage.removeItem('algorithmic_token');
            authToken = null;
            currentBranchId = null;
            document.getElementById('appContainer').classList.add('hidden');
            document.getElementById('authOverlay').classList.remove('hidden');
            document.getElementById('loginForm').classList.remove('hidden');
            document.getElementById('signupForm').classList.add('hidden');
        }

        // Wraps fetch to attach the auth token and bounce to login on 401.
        async function authFetch(url, options = {}) {
            options.headers = options.headers || {};
            options.headers['Authorization'] = `Bearer ${authToken}`;
            const res = await fetch(url, options);
            if (res.status === 401) {
                localStorage.removeItem('algorithmic_token');
                authToken = null;
                document.getElementById('appContainer').classList.add('hidden');
                document.getElementById('authOverlay').classList.remove('hidden');
                document.getElementById('loginError').textContent = 'Session expired. Please log in again.';
                throw new Error('Session expired');
            }
            return res;
        }

        // Try to resume a session on page load if a token is already saved.
        (async function tryResumeSession() {
            if (!authToken) return;
            try {
                const res = await authFetch('/api/auth/me');
                if (res.ok) {
                    const data = await res.json();
                    applyIdentity(data);
                    document.getElementById('authOverlay').classList.add('hidden');
                    document.getElementById('appContainer').classList.remove('hidden');
                    initApp();
                }
            } catch (e) { /* handled in authFetch */ }
        })();

        // ---- Branches ----

        async function loadBranches() {
            const res = await authFetch('/api/branches');
            branches = await res.json();
            const selector = document.getElementById('branchSelector');
            selector.innerHTML = '';
            branches.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b.id;
                opt.textContent = b.name;
                if (currentBranchId === b.id) opt.selected = true;
                selector.appendChild(opt);
            });
            if (!currentBranchId && branches.length > 0) {
                currentBranchId = branches[0].id;
                selector.value = currentBranchId;
            }
        }

        document.getElementById('branchSelector').addEventListener('change', (e) => {
            currentBranchId = parseInt(e.target.value);
            refreshCurrentModule();
        });

        function openAddBranchModal() { document.getElementById('branchModal').classList.remove('hidden'); }
        function closeAddBranchModal() { document.getElementById('branchModal').classList.add('hidden'); document.getElementById('newBranchName').value = ''; }

        async function createNewBranch() {
            const name = document.getElementById('newBranchName').value.trim();
            if (!name) return;
            const res = await authFetch('/api/branches', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            if (res.ok) {
                const newBranch = await res.json();
                closeAddBranchModal();
                await loadBranches();
                currentBranchId = newBranch.id;
                document.getElementById('branchSelector').value = currentBranchId;
                refreshCurrentModule();
            } else {
                alert('Branch already exists or invalid name.');
            }
        }

        function switchModule(moduleName) {
            currentModule = moduleName;
            document.querySelectorAll('.sidebar-item').forEach(btn => btn.classList.remove('active'));
            if (window.event && window.event.currentTarget) window.event.currentTarget.classList.add('active');
            refreshCurrentModule();
        }

        async function initApp() {
            await loadBranches();
            refreshCurrentModule();
        }

        async function refreshCurrentModule() {
            const container = document.getElementById('mainContent');
            if (currentModule === 'home') {
                renderHomeModule(container);
            } else if (currentModule === 'timetables') {
                renderTimetableModule(container);
            } else if (currentModule === 'users') {
                await renderUsersModule(container);
            } else {
                await renderDataModule(container, currentModule);
            }
        }

        function renderHomeModule(container) {
            container.innerHTML = `
                <div class="space-y-8">
                    <div class="glass-panel border gold-border p-10 rounded-3xl relative overflow-hidden shadow-2xl">
                        <div class="max-w-3xl relative z-10 space-y-4">
                            <span class="text-xs uppercase tracking-widest px-3 py-1 rounded-full bg-[#1c1c1c] gold-gradient-text border gold-border font-extrabold">Executive Command Center</span>
                            <h2 class="text-4xl font-black text-white tracking-tight leading-tight">Institutional Operations, <span class="gold-gradient-text">Mastered.</span></h2>
                            <p class="text-lg text-gray-300 font-medium leading-relaxed pt-2">We simplify the boring clerical work. Not by hiring more clerks, but by never needing to do so.</p>
                            <div class="pt-4 flex items-center space-x-4">
                                <button onclick="switchModule('students')" class="gold-bg hover:opacity-95 text-black font-extrabold px-6 py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Manage Students</button>
                                <button onclick="switchModule('fees')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-extrabold px-6 py-3 rounded-xl text-xs uppercase tracking-wider fast-transition">View Fees (INR ₹)</button>
                            </div>
                        </div>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
                        <div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Active Students</div><div class="text-3xl font-black gold-gradient-text" id="statStudents">—</div></div>
                        <div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Faculty Members</div><div class="text-3xl font-black gold-gradient-text" id="statTeachers">—</div></div>
                        <div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Classrooms Available</div><div class="text-3xl font-black gold-gradient-text" id="statClassrooms">—</div></div>
                        <div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Fee Collection (INR)</div><div class="text-3xl font-black gold-gradient-text" id="statFees">₹0</div></div>
                    </div>
                </div>
            `;
            loadHomeStats();
        }

        async function loadHomeStats() {
            try {
                if (!currentBranchId) return;
                const [sRes, tRes, cRes, fRes] = await Promise.all([
                    authFetch(`/api/records/students/${currentBranchId}`),
                    authFetch(`/api/records/teachers/${currentBranchId}`),
                    authFetch(`/api/records/classrooms/${currentBranchId}`),
                    authFetch(`/api/records/fees/${currentBranchId}`)
                ]);
                document.getElementById('statStudents').textContent = (await sRes.json()).length;
                document.getElementById('statTeachers').textContent = (await tRes.json()).length;
                document.getElementById('statClassrooms').textContent = (await cRes.json()).length;
                const fees = await fRes.json();
                const total = fees.reduce((acc, curr) => acc + (curr.amount_inr || 0), 0);
                document.getElementById('statFees').textContent = `₹${total.toLocaleString('en-IN')}`;
            } catch (e) { console.error(e); }
        }

        async function renderDataModule(container, moduleName) {
            const canWrite = myPermission !== 'read_only';
            container.innerHTML = `
                <div class="space-y-6">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">${moduleName} Department</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Branch Synchronized • Document Supported</p>
                        </div>
                        ${canWrite ? `
                        <div class="flex items-center space-x-3">
                            <button onclick="openRecordModal('${moduleName}')" class="gold-bg hover:opacity-95 text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add New Record</button>
                            <button onclick="openBulkImportModal('${moduleName}')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add Document</button>
                        </div>` : ''}
                    </div>
                    <div class="glass-panel border gold-border rounded-2xl p-6 overflow-x-auto shadow-2xl">
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead id="moduleTableHead" class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border"></thead>
                            <tbody id="moduleTableBody"></tbody>
                        </table>
                    </div>
                </div>
            `;
            await loadModuleRecords(moduleName);
        }

        async function loadModuleRecords(moduleName) {
            if (!currentBranchId) return;
            const canWrite = myPermission !== 'read_only';
            const res = await authFetch(`/api/records/${moduleName}/${currentBranchId}`);
            const records = await res.json();
            const thead = document.getElementById('moduleTableHead');
            const tbody = document.getElementById('moduleTableBody');

            // 'building' is retired from classrooms, and record ownership fields never render.
            const hiddenKeys = ['id', 'branch_id', 'building'];

            if (records.length === 0) {
                thead.innerHTML = `<tr><th class="p-4">Status</th></tr>`;
                tbody.innerHTML = `<tr><td class="p-8 text-center text-gray-500">No records found for ${moduleName}. ${canWrite ? "Click '+ Add New Record' to create one." : ''}</td></tr>`;
                return;
            }

            const keys = Object.keys(records[0]).filter(k => !hiddenKeys.includes(k));
            thead.innerHTML = `<tr>${keys.map(k => `<th class="p-4 uppercase tracking-wider text-xs font-bold">${k.replace('_', ' ')}</th>`).join('')}${canWrite ? '<th class="p-4"></th>' : ''}</tr>`;
            tbody.innerHTML = records.map(r => `
                <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                    ${keys.map(k => {
                        let val = r[k];
                        if (moduleName === 'fees' && k === 'amount_inr') { val = `₹${parseFloat(val || 0).toLocaleString('en-IN')}`; }
                        if (k === 'document' && val) { val = `<a href="/uploads/${val}" target="_blank" class="text-yellow-500 underline text-xs font-semibold">View File</a>`; }
                        else if (k === 'document' && !val) { val = `<span class="text-gray-600 text-xs">No File</span>`; }
                        return `<td class="p-4 font-medium">${val ?? ''}</td>`;
                    }).join('')}
                    ${canWrite ? `<td class="p-4 text-right"><button onclick="deleteRecord('${moduleName}', ${r.id})" title="Delete record" class="row-delete-btn fast-transition text-lg leading-none">🗑</button></td>` : ''}
                </tr>
            `).join('');
        }

        async function deleteRecord(moduleName, recordId) {
            if (!confirm('Remove this record permanently? This cannot be undone.')) return;
            const res = await authFetch(`/api/records/${moduleName}/${recordId}`, { method: 'DELETE' });
            if (res.ok) {
                await loadModuleRecords(moduleName);
            } else {
                const err = await res.json().catch(() => ({}));
                alert(err.detail || 'Failed to delete record.');
            }
        }

        // ---- Bulk import ("+ Add Document") ----

        function openBulkImportModal(moduleName) {
            bulkImportModule = moduleName;
            document.getElementById('bulkImportTitle').textContent = `Bulk Import ${moduleName}`;
            document.getElementById('bulkImportColumns').textContent = BULK_IMPORT_COLUMNS[moduleName].join(', ');
            document.getElementById('bulkImportFile').value = '';
            document.getElementById('bulkImportError').textContent = '';
            document.getElementById('bulkImportModal').classList.remove('hidden');
        }
        function closeBulkImportModal() { document.getElementById('bulkImportModal').classList.add('hidden'); }

        async function submitBulkImport() {
            const errorEl = document.getElementById('bulkImportError');
            errorEl.textContent = '';
            const fileInput = document.getElementById('bulkImportFile');
            if (!fileInput.files[0]) { errorEl.textContent = 'Please choose a CSV file first.'; return; }

            const formData = new FormData();
            formData.append('branch_id', currentBranchId);
            formData.append('file', fileInput.files[0]);

            const res = await authFetch(`/api/records/${bulkImportModule}/bulk`, { method: 'POST', body: formData });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                closeBulkImportModal();
                await loadModuleRecords(bulkImportModule);
                alert(`Imported ${data.inserted} record(s) successfully.`);
            } else {
                errorEl.textContent = data.detail || 'Import failed.';
            }
        }

        const BULK_IMPORT_COLUMNS = {
            students: ['name', 'course', 'status'],
            teachers: ['name', 'subject', 'department'],
            classrooms: ['room_no', 'capacity'],
            syllabus: ['subject', 'semester', 'units'],
            attendance: ['student_name', 'date', 'status'],
            invigilation: ['teacher_name', 'exam_date', 'room'],
            fees: ['student_name', 'amount_inr', 'status', 'due_date'],
        };

        // ---- Institute rename ----

        function openRenameInstituteModal() {
            if (myPermission === 'read_only') return;
            document.getElementById('renameInstituteInput').value = document.getElementById('headerInstituteName').textContent.trim();
            document.getElementById('renameInstituteError').textContent = '';
            document.getElementById('renameInstituteModal').classList.remove('hidden');
        }
        function closeRenameInstituteModal() { document.getElementById('renameInstituteModal').classList.add('hidden'); }

        async function submitRenameInstitute() {
            const errorEl = document.getElementById('renameInstituteError');
            const name = document.getElementById('renameInstituteInput').value.trim();
            if (!name) { errorEl.textContent = 'Institute name cannot be empty.'; return; }
            const res = await authFetch('/api/institute/name', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ institute_name: name })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                document.getElementById('headerInstituteName').textContent = data.institute_name;
                closeRenameInstituteModal();
            } else {
                errorEl.textContent = data.detail || 'Failed to rename institute.';
            }
        }

        // ---- Manage Users ----

        async function renderUsersModule(container) {
            container.innerHTML = `
                <div class="space-y-6">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Manage Users</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Grant, monitor, and revoke staff access</p>
                        </div>
                        <button onclick="openUserModal()" class="gold-bg hover:opacity-95 text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add User</button>
                    </div>
                    <div class="glass-panel border gold-border rounded-2xl p-6 overflow-x-auto shadow-2xl">
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border">
                                <tr><th class="p-4">Full Name</th><th class="p-4">Email</th><th class="p-4">Permission</th><th class="p-4"></th></tr>
                            </thead>
                            <tbody id="usersTableBody"></tbody>
                        </table>
                    </div>
                </div>
            `;
            await loadUsers();
        }

        async function loadUsers() {
            const res = await authFetch('/api/users');
            const users = await res.json();
            const tbody = document.getElementById('usersTableBody');
            if (users.length === 0) {
                tbody.innerHTML = `<tr><td colspan="4" class="p-8 text-center text-gray-500">No staff users yet. Click '+ Add User' to grant access.</td></tr>`;
                return;
            }
            tbody.innerHTML = users.map(u => `
                <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                    <td class="p-4 font-medium text-white">${u.full_name}</td>
                    <td class="p-4 text-gray-400">${u.email}</td>
                    <td class="p-4">
                        <select onchange="changeUserPermission(${u.id}, this.value)" class="bg-[#0c0c0c] border gold-border rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none">
                            <option value="edit" ${u.permission === 'edit' ? 'selected' : ''}>Edit Access</option>
                            <option value="read_only" ${u.permission === 'read_only' ? 'selected' : ''}>Read Only</option>
                        </select>
                    </td>
                    <td class="p-4 text-right"><button onclick="removeUser(${u.id})" title="Revoke access" class="row-delete-btn fast-transition text-lg leading-none">🗑</button></td>
                </tr>
            `).join('');
        }

        function openUserModal() {
            document.getElementById('newUserName').value = '';
            document.getElementById('newUserEmail').value = '';
            document.getElementById('newUserPassword').value = '';
            document.getElementById('newUserPermission').value = 'edit';
            document.getElementById('userModalError').textContent = '';
            document.getElementById('userModal').classList.remove('hidden');
        }
        function closeUserModal() { document.getElementById('userModal').classList.add('hidden'); }

        async function submitNewUser() {
            const errorEl = document.getElementById('userModalError');
            const full_name = document.getElementById('newUserName').value.trim();
            const email = document.getElementById('newUserEmail').value.trim();
            const password = document.getElementById('newUserPassword').value;
            const permission = document.getElementById('newUserPermission').value;
            if (!full_name || !email || !password) { errorEl.textContent = 'All fields are required.'; return; }

            const res = await authFetch('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ full_name, email, password, permission })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                closeUserModal();
                await loadUsers();
            } else {
                errorEl.textContent = data.detail || 'Failed to create user.';
            }
        }

        async function changeUserPermission(userId, permission) {
            const res = await authFetch(`/api/users/${userId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ permission })
            });
            if (!res.ok) { alert('Failed to update permission.'); await loadUsers(); }
        }

        async function removeUser(userId) {
            if (!confirm('Revoke this user\\'s access permanently?')) return;
            const res = await authFetch(`/api/users/${userId}`, { method: 'DELETE' });
            if (res.ok) { await loadUsers(); } else { alert('Failed to remove user.'); }
        }

        async function renderTimetableModule(container) {
            const tRes = await authFetch(`/api/records/teachers/${currentBranchId}`);
            const teachers = await tRes.json();
            const sRes = await authFetch(`/api/timetable/slots/${currentBranchId}`);
            const savedSlots = await sRes.json();

            container.innerHTML = `
                <div class="space-y-8">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Timetable Generation & Batch Scheduler</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Conflict-checked scheduler (teacher & room aware)</p>
                        </div>
                        <button onclick="window.print()" class="bg-[#141414] hover:bg-[#202020] gold-gradient-text border gold-border px-5 py-2.5 rounded-xl text-xs font-extrabold uppercase tracking-wider fast-transition shadow-lg">Download PDF / Print Timetable</button>
                    </div>
                    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                        <div class="glass-panel border gold-border p-6 rounded-2xl space-y-6">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Configure Batch & Teacher Load</h3>
                            <div class="space-y-4">
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Batch Name</label>
                                    <input type="text" id="ttBatchName" placeholder="e.g. B.Tech CSE Batch A" value="B.Tech CSE Batch A" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                                </div>
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Lecture Timings (Comma separated)</label>
                                    <input type="text" id="ttTimings" value="09:00 AM - 10:00 AM, 10:00 AM - 11:00 AM, 11:15 AM - 12:15 PM, 01:15 PM - 02:15 PM" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-xs text-gray-200 gold-border-glow focus:outline-none">
                                </div>
                                <div class="pt-2">
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Assigned Teachers & Constraints</label>
                                    <div id="teacherConfigList" class="space-y-3 max-h-60 overflow-y-auto pr-2">
                                        ${teachers.length === 0 ? '<p class="text-xs text-gray-500">No teachers found. Please add teachers first.</p>' :
                                          teachers.map((t, idx) => `
                                            <div class="p-3 bg-[#0f0f0f] border gold-border rounded-xl space-y-2" data-teacher="${t.name}" data-subject="${t.subject}">
                                                <div class="flex justify-between items-center text-xs font-bold text-gray-200"><span>${t.name} (${t.subject})</span></div>
                                                <div class="grid grid-cols-2 gap-2">
                                                    <div><label class="text-[10px] text-gray-400 uppercase">Lectures/Week</label><input type="number" id="lec_${idx}" value="3" min="1" max="5" class="w-full bg-[#070707] border gold-border rounded p-1.5 text-xs text-white"></div>
                                                    <div><label class="text-[10px] text-gray-400 uppercase">Unavailable Days</label><input type="text" id="unav_${idx}" placeholder="e.g. Monday" class="w-full bg-[#070707] border gold-border rounded p-1.5 text-xs text-white" title="Comma separated days"></div>
                                                </div>
                                            </div>
                                          `).join('')}
                                    </div>
                                </div>
                                <button onclick="generateTimetableSchedule()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Generate Weekly Timetable</button>
                            </div>
                        </div>
                        <div class="lg:col-span-2 glass-panel border gold-border p-6 rounded-2xl overflow-x-auto">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider mb-4">Generated Weekly Schedule</h3>
                            <table class="w-full text-left text-sm text-gray-300">
                                <thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border"><tr><th class="p-3">Day</th><th class="p-3">Time Slot</th><th class="p-3">Subject</th><th class="p-3">Teacher</th><th class="p-3">Room</th></tr></thead>
                                <tbody id="timetableSlotsBody">
                                    ${savedSlots.length === 0 ? '<tr><td colspan="5" class="p-6 text-center text-gray-500">No timetable generated yet. Configure and click generate.</td></tr>' :
                                      savedSlots.map(s => `
                                        <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                                            <td class="p-3 font-semibold text-yellow-500">${s.day}</td><td class="p-3">${s.time_slot}</td><td class="p-3 font-medium">${s.subject}</td><td class="p-3">${s.teacher}</td><td class="p-3 text-xs text-gray-400">${s.room}</td>
                                        </tr>
                                      `).join('')}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
        }

        async function generateTimetableSchedule() {
            const batchName = document.getElementById('ttBatchName').value;
            const timingsRaw = document.getElementById('ttTimings').value;
            const timings = timingsRaw.split(',').map(s => s.trim()).filter(Boolean);
            const teacherElements = document.querySelectorAll('#teacherConfigList > div');
            const teachers_config = [];
            teacherElements.forEach((el, idx) => {
                const name = el.getAttribute('data-teacher');
                const subject = el.getAttribute('data-subject');
                const lectures_per_week = document.getElementById(`lec_${idx}`).value;
                const unavRaw = document.getElementById(`unav_${idx}`).value;
                const unavailable_days = unavRaw.split(',').map(s => s.trim()).filter(Boolean);
                teachers_config.push({ name, subject, lectures_per_week, unavailable_days });
            });

            const res = await authFetch('/api/timetable/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ branch_id: currentBranchId, batch_name: batchName, teachers_config, timings })
            });

            if (res.ok) {
                const result = await res.json();
                let msg = 'Timetable successfully generated and saved!';
                if (result.warnings && result.warnings.length > 0) {
                    msg += '\\n\\nHeads up:\\n' + result.warnings.join('\\n');
                }
                alert(msg);
                refreshCurrentModule();
            } else {
                alert('Failed to generate timetable.');
            }
        }

        function openRecordModal(moduleName) {
            document.getElementById('recordModal').classList.remove('hidden');
            document.getElementById('modalTitle').textContent = `Add New ${moduleName} Record`;
            document.getElementById('recordFormError').textContent = '';
            const fieldsContainer = document.getElementById('modalFields');

            let fieldsConfig = [];
            if (moduleName === 'students') {
                fieldsConfig = [
                    { id: 'name', label: 'Full Name', type: 'text', placeholder: 'Aarav Sharma' },
                    { id: 'course', label: 'Course / Program', type: 'text', placeholder: 'B.Tech Computer Science' },
                    { id: 'status', label: 'Status', type: 'text', placeholder: 'Active' }
                ];
            } else if (moduleName === 'teachers') {
                fieldsConfig = [
                    { id: 'name', label: 'Teacher Name', type: 'text', placeholder: 'Dr. Ramesh Kumar' },
                    { id: 'subject', label: 'Specialization', type: 'text', placeholder: 'Artificial Intelligence' },
                    { id: 'department', label: 'Department', type: 'text', placeholder: 'School of Engineering' }
                ];
            } else if (moduleName === 'classrooms') {
                fieldsConfig = [
                    { id: 'room_no', label: 'Room Number', type: 'text', placeholder: 'Lecture Hall 402' },
                    { id: 'capacity', label: 'Seating Capacity', type: 'number', placeholder: '120' }
                ];
            } else if (moduleName === 'syllabus') {
                fieldsConfig = [
                    { id: 'subject', label: 'Subject Name', type: 'text', placeholder: 'Data Structures & Algorithms' },
                    { id: 'semester', label: 'Semester', type: 'text', placeholder: 'Fall 2026' },
                    { id: 'units', label: 'Credit Units', type: 'number', placeholder: '4' }
                ];
            } else if (moduleName === 'attendance') {
                fieldsConfig = [
                    { id: 'student_name', label: 'Student Name', type: 'text', placeholder: 'Priya Patel' },
                    { id: 'date', label: 'Date', type: 'text', placeholder: '2026-09-01' },
                    { id: 'status', label: 'Attendance Status', type: 'text', placeholder: 'Present' }
                ];
            } else if (moduleName === 'invigilation') {
                fieldsConfig = [
                    { id: 'teacher_name', label: 'Faculty Name', type: 'text', placeholder: 'Prof. Vikram Joshi' },
                    { id: 'exam_date', label: 'Exam Date', type: 'text', placeholder: '2026-09-15' },
                    { id: 'room', label: 'Exam Hall', type: 'text', placeholder: 'Examination Block B' }
                ];
            } else if (moduleName === 'fees') {
                fieldsConfig = [
                    { id: 'student_name', label: 'Student Name', type: 'text', placeholder: 'Rohan Sharma' },
                    { id: 'amount_inr', label: 'Fee Amount (INR ₹)', type: 'number', placeholder: '75000' },
                    { id: 'status', label: 'Payment Status', type: 'text', placeholder: 'Paid / Pending' },
                    { id: 'due_date', label: 'Due Date', type: 'text', placeholder: '2026-09-30' }
                ];
            }

            fieldsContainer.innerHTML = fieldsConfig.map(f => `
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">${f.label}</label>
                    <input type="${f.type}" id="field_${f.id}" required placeholder="${f.placeholder}" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
            `).join('');

            window.activeModalModule = moduleName;
        }

        function closeRecordModal() { document.getElementById('recordModal').classList.add('hidden'); }

        async function submitRecordForm(e) {
            e.preventDefault();
            const errorEl = document.getElementById('recordFormError');
            errorEl.textContent = '';
            const moduleName = window.activeModalModule;
            const inputs = document.getElementById('modalFields').querySelectorAll('input');
            const data = {};
            inputs.forEach(input => { const key = input.id.replace('field_', ''); data[key] = input.type === 'number' ? parseFloat(input.value) : input.value; });

            const formData = new FormData();
            formData.append('branch_id', currentBranchId);
            formData.append('data_json', JSON.stringify(data));

            const res = await authFetch(`/api/records/${moduleName}`, { method: 'POST', body: formData });

            if (res.ok) {
                closeRecordModal();
                await loadModuleRecords(moduleName);
            } else {
                const errData = await res.json().catch(() => ({}));
                errorEl.textContent = errData.detail || 'Failed to save record.';
            }
        }
    </script>
</body>
</html>
"""
