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
SEATING_MODULE = 'seating'

# 'timetables' isn't a generic /api/records table (it has its own dedicated
# endpoints below) but it IS a sidebar module a staff designation can be
# granted or denied access to, so it's included here for permission checks.
ALL_ACCESS_MODULES = VALID_MODULES + ['timetables', SEATING_MODULE]

DESIGNATION_PRESETS = ['Admin', 'Accountant', 'Teacher', 'Head', 'Clerk', 'Custom']


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
    # Migration: designation (label like "Admin"/"Teacher") and module_access
    # (JSON list of module keys this staff member may open; NULL/'all' = every
    # module) for pre-existing DBs created before per-designation access existed.
    for col, coltype in [("designation", "TEXT"), ("module_access", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE staff_users ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
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
            batch TEXT,
            status TEXT,
            document TEXT,
            roll_number TEXT,
            parent_contact TEXT,
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
            contact_number TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    # Migration for pre-existing DBs: 'course' was renamed to 'batch', and
    # students/teachers/syllabus each gained the new fields below.
    try:
        cursor.execute("ALTER TABLE students RENAME COLUMN course TO batch")
    except sqlite3.OperationalError:
        pass
    for col, coltype in [("roll_number", "TEXT"), ("parent_contact", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE students ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    try:
        cursor.execute("ALTER TABLE teachers ADD COLUMN contact_number TEXT")
    except sqlite3.OperationalError:
        pass
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
            topic TEXT,
            teacher_name TEXT,
            num_lectures INTEGER,
            lecture_date TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    # Migration for pre-existing DBs created before these syllabus fields existed.
    for col, coltype in [("topic", "TEXT"), ("teacher_name", "TEXT"), ("num_lectures", "INTEGER"), ("lecture_date", "TEXT")]:
        try:
            cursor.execute(f"ALTER TABLE syllabus ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
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
            lecture_number INTEGER,
            subject TEXT,
            teacher TEXT,
            room TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    # Migration for pre-existing DBs created before lecture_number existed.
    try:
        cursor.execute("ALTER TABLE timetables_slots ADD COLUMN lecture_number INTEGER")
    except sqlite3.OperationalError:
        pass
    # Stores the exact teacher/timing prerequisites used for a batch's
    # timetable, so it can be reloaded and regenerated without the user
    # having to retype (and possibly get wrong) lectures-per-week/unavailable
    # days a second time.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS timetable_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            batch_name TEXT NOT NULL,
            timings_json TEXT NOT NULL,
            teachers_config_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(branch_id, batch_name),
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_seatings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            exam_date TEXT NOT NULL,
            room_number TEXT NOT NULL,
            rows INTEGER NOT NULL,
            columns INTEGER NOT NULL,
            assignments_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
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
    designation: str = "Owner"
    allowed_modules: list = ALL_ACCESS_MODULES  # modules this login may open in the sidebar


def check_module_access(institute: "CurrentInstitute", module: str):
    """Owners always pass. Staff logins are blocked from any module their
    designation wasn't explicitly granted."""
    if institute.is_owner:
        return
    if module not in institute.allowed_modules:
        raise HTTPException(status_code=403, detail=f"Your account does not have access to the {module.title()} module")


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
        raw_access = staff["module_access"] if "module_access" in staff.keys() else None
        try:
            allowed = json.loads(raw_access) if raw_access else []
        except (TypeError, ValueError):
            allowed = []
        return CurrentInstitute(
            id=institute["id"],
            institute_name=institute["institute_name"],
            full_name=staff["full_name"],
            email=staff["email"],
            is_owner=False,
            permission=staff["permission"],
            designation=(staff["designation"] if "designation" in staff.keys() and staff["designation"] else "Staff"),
            allowed_modules=allowed,
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
        "designation": "Owner",
        "allowed_modules": ALL_ACCESS_MODULES,
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
                "designation": "Owner",
                "allowed_modules": ALL_ACCESS_MODULES,
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
            try:
                staff_modules = json.loads(staff["module_access"]) if staff["module_access"] else []
            except (TypeError, ValueError):
                staff_modules = []
            return {
                "token": token,
                "institute_name": parent_institute["institute_name"] if parent_institute else "",
                "full_name": staff["full_name"],
                "is_owner": False,
                "permission": staff["permission"],
                "designation": staff["designation"] or "Staff",
                "allowed_modules": staff_modules,
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
        "designation": institute.designation,
        "allowed_modules": institute.allowed_modules,
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
    designation: str  # e.g. 'Admin', 'Accountant', 'Teacher', 'Head', 'Clerk', 'Custom'
    modules: list = []  # which sidebar modules this designation may open


class StaffPermissionUpdate(BaseModel):
    permission: str | None = None
    designation: str | None = None
    modules: list | None = None


def _validate_modules(modules: list):
    bad = [m for m in modules if m not in ALL_ACCESS_MODULES]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown module(s): {', '.join(bad)}")


@app.get("/api/users")
def list_staff_users(institute: CurrentInstitute = Depends(require_owner)):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, full_name, email, permission, designation, module_access, created_at FROM staff_users WHERE institute_id = ?",
        (institute.id,),
    )
    users = []
    for row in cursor.fetchall():
        u = dict(row)
        try:
            u["modules"] = json.loads(u.pop("module_access") or "[]")
        except ValueError:
            u["modules"] = []
        u["designation"] = u.get("designation") or "Staff"
        users.append(u)
    conn.close()
    return users


@app.post("/api/users")
def add_staff_user(req: StaffUserCreate, institute: CurrentInstitute = Depends(require_owner)):
    if req.permission not in ("edit", "read_only"):
        raise HTTPException(status_code=400, detail="Permission must be 'edit' or 'read_only'")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not req.designation.strip():
        raise HTTPException(status_code=400, detail="Designation is required")
    _validate_modules(req.modules)

    salt = secrets.token_hex(16)
    password_hash = hash_password(req.password, salt)

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO staff_users (institute_id, full_name, email, password_hash, password_salt, permission, designation, module_access, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (institute.id, req.full_name, req.email.lower(), password_hash, salt, req.permission,
             req.designation.strip(), json.dumps(req.modules), datetime.utcnow().isoformat()),
        )
        conn.commit()
        user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="A user with this email already exists")
    conn.close()
    return {"id": user_id, "full_name": req.full_name, "email": req.email.lower(), "permission": req.permission,
            "designation": req.designation.strip(), "modules": req.modules}


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
    """Partial update - the boss can change permission, designation, and/or
    module access (grant/revoke) independently or all at once."""
    verify_staff_ownership(user_id, institute.id)

    updates, params = [], []
    if req.permission is not None:
        if req.permission not in ("edit", "read_only"):
            raise HTTPException(status_code=400, detail="Permission must be 'edit' or 'read_only'")
        updates.append("permission = ?")
        params.append(req.permission)
    if req.designation is not None:
        if not req.designation.strip():
            raise HTTPException(status_code=400, detail="Designation cannot be empty")
        updates.append("designation = ?")
        params.append(req.designation.strip())
    if req.modules is not None:
        _validate_modules(req.modules)
        updates.append("module_access = ?")
        params.append(json.dumps(req.modules))

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    conn = get_conn()
    conn.execute(f"UPDATE staff_users SET {', '.join(updates)} WHERE id = ?", (*params, user_id))
    conn.commit()
    conn.close()
    return {"id": user_id, "status": "updated"}


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
    check_module_access(institute, module)
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


# Non-document columns each module accepts from the client, in the order
# they're bound into INSERT/UPDATE statements. Shared by add_record and
# edit_record so the two can never drift out of sync with each other.
RECORD_FIELDS = {
    "students": ["name", "batch", "roll_number", "parent_contact"],
    "teachers": ["name", "subject", "contact_number"],
    "classrooms": ["room_no", "capacity", "building"],
    "syllabus": ["subject", "topic", "teacher_name", "num_lectures", "lecture_date"],
    "attendance": ["student_name", "date", "status"],
    "invigilation": ["teacher_name", "exam_date", "room"],
    "fees": ["student_name", "amount_inr", "status", "due_date"],
}
# Modules whose table has a 'document' column that a file upload fills in.
RECORD_HAS_DOCUMENT = {"classrooms", "attendance", "invigilation", "fees"}


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
    check_module_access(institute, module)
    verify_branch_ownership(branch_id, institute.id)

    data = json.loads(data_json)
    doc_filename = save_upload(file) if file else None

    fields = RECORD_FIELDS[module]
    columns = ["branch_id"] + fields + (["document"] if module in RECORD_HAS_DOCUMENT else [])
    values = [branch_id] + [data.get(f) for f in fields] + ([doc_filename] if module in RECORD_HAS_DOCUMENT else [])
    placeholders = ", ".join("?" for _ in columns)

    conn = get_conn()
    cursor = conn.cursor()
    # module/columns come from our own fixed RECORD_FIELDS map, never from the
    # request, so building the column list this way is not injectable.
    cursor.execute(f"INSERT INTO {module} ({', '.join(columns)}) VALUES ({placeholders})", values)
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return {"id": record_id, "status": "success"}


@app.patch("/api/records/{module}/{record_id}")
async def edit_record(
    module: str,
    record_id: int,
    data_json: str = Form(...),
    file: UploadFile = File(None),
    institute: CurrentInstitute = Depends(require_write_access),
):
    """Generic edit for any module - lets the user change any field on an
    existing record, and optionally replace its attached document."""
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    check_module_access(institute, module)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT {module}.id FROM {module}
            JOIN branches ON branches.id = {module}.branch_id
            WHERE {module}.id = ? AND branches.institute_id = ?""",
        (record_id, institute.id),
    )
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Record not found")

    data = json.loads(data_json)
    fields = RECORD_FIELDS[module]
    set_clauses = [f"{f} = ?" for f in fields]
    values = [data.get(f) for f in fields]

    if module in RECORD_HAS_DOCUMENT and file:
        set_clauses.append("document = ?")
        values.append(save_upload(file))

    cursor.execute(f"UPDATE {module} SET {', '.join(set_clauses)} WHERE id = ?", (*values, record_id))
    conn.commit()
    conn.close()
    return {"id": record_id, "status": "updated"}


@app.delete("/api/records/{module}/{record_id}")
def delete_record(module: str, record_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    check_module_access(institute, module)

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
    "syllabus": ["subject", "topic", "teacher_name", "num_lectures", "lecture_date"],
    "attendance": ["student_name", "date", "status"],
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
    check_module_access(institute, module)
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
            cursor.execute("INSERT INTO students (branch_id, name, batch, roll_number, parent_contact) VALUES (?, ?, ?, ?, ?)",
                           (branch_id, row.get('name'), row.get('batch'), row.get('roll_number'), row.get('parent_contact')))
        elif module == 'teachers':
            cursor.execute("INSERT INTO teachers (branch_id, name, subject, contact_number) VALUES (?, ?, ?, ?)",
                           (branch_id, row.get('name'), row.get('subject'), row.get('contact_number')))
        elif module == 'classrooms':
            cursor.execute("INSERT INTO classrooms (branch_id, room_no, capacity, building, document) VALUES (?, ?, ?, ?, ?)",
                           (branch_id, row.get('room_no'), row.get('capacity'), None, None))
        elif module == 'syllabus':
            cursor.execute("INSERT INTO syllabus (branch_id, subject, topic, teacher_name, num_lectures, lecture_date) VALUES (?, ?, ?, ?, ?, ?)",
                           (branch_id, row.get('subject'), row.get('topic'), row.get('teacher_name'), row.get('num_lectures'), row.get('lecture_date')))
        elif module == 'attendance':
            cursor.execute("INSERT INTO attendance (branch_id, student_name, date, status, document) VALUES (?, ?, ?, ?, ?)",
                           (branch_id, row.get('student_name'), row.get('date'), row.get('status'), None))
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
# Attendance (batchwise present/absent marking, sourced from Student Department)
# ---------------------------------------------------------------------------

class AttendanceMarkRequest(BaseModel):
    branch_id: int
    student_name: str
    date: str
    status: str  # 'Present' or 'Absent'


@app.post("/api/attendance/mark")
def mark_attendance(req: AttendanceMarkRequest, institute: CurrentInstitute = Depends(require_write_access)):
    check_module_access(institute, "attendance")
    verify_branch_ownership(req.branch_id, institute.id)
    if req.status not in ("Present", "Absent"):
        raise HTTPException(status_code=400, detail="Status must be 'Present' or 'Absent'")

    conn = get_conn()
    cursor = conn.cursor()
    # Re-marking the same student on the same day replaces the old mark
    # instead of piling up duplicate attendance rows.
    cursor.execute(
        "DELETE FROM attendance WHERE branch_id = ? AND student_name = ? AND date = ?",
        (req.branch_id, req.student_name, req.date),
    )
    cursor.execute(
        "INSERT INTO attendance (branch_id, student_name, date, status) VALUES (?, ?, ?, ?)",
        (req.branch_id, req.student_name, req.date, req.status),
    )
    conn.commit()
    conn.close()
    return {"status": "success"}


@app.get("/api/attendance/{branch_id}/{date}")
def get_attendance_for_date(branch_id: int, date: str, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, "attendance")
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT student_name, status FROM attendance WHERE branch_id = ? AND date = ?", (branch_id, date))
    marks = {row["student_name"]: row["status"] for row in cursor.fetchall()}
    conn.close()
    return marks


@app.get("/api/attendance/history/{branch_id}")
def get_attendance_history(branch_id: int, student_name: str, institute: CurrentInstitute = Depends(get_current_institute)):
    """Full past attendance record for one student, most recent date first -
    the 'view attendance report for each student' feature."""
    check_module_access(institute, "attendance")
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT date, status FROM attendance WHERE branch_id = ? AND student_name = ? ORDER BY date DESC",
        (branch_id, student_name),
    )
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    present = sum(1 for h in history if h["status"] == "Present")
    return {
        "student_name": student_name,
        "history": history,
        "total_marked": len(history),
        "present_count": present,
        "absent_count": len(history) - present,
    }


# ---------------------------------------------------------------------------
# Timetable generation
# ---------------------------------------------------------------------------

@app.get("/api/timetable/slots/{branch_id}")
def get_timetable_slots(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, "timetables")
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM timetables_slots WHERE branch_id = ? ORDER BY batch_name, lecture_number", (branch_id,))
    slots = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return slots


@app.get("/api/timetable/configs/{branch_id}")
def list_timetable_configs(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    """The saved prerequisites (timings + per-teacher lectures/unavailable
    days) for every batch that's had a timetable generated, so the frontend
    can offer 'load this batch to edit & regenerate' without the user
    retyping anything."""
    check_module_access(institute, "timetables")
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT batch_name, timings_json, teachers_config_json FROM timetable_configs WHERE branch_id = ? ORDER BY batch_name", (branch_id,))
    configs = []
    for row in cursor.fetchall():
        configs.append({
            "batch_name": row["batch_name"],
            "timings": json.loads(row["timings_json"]),
            "teachers_config": json.loads(row["teachers_config_json"]),
        })
    conn.close()
    return configs


class TimingSlot(BaseModel):
    lecture_number: int
    time_slot: str  # e.g. "09:00 AM - 10:00 AM"


class TimetableGenerateRequest(BaseModel):
    branch_id: int
    batch_name: str
    teachers_config: list  # [{name, subject, lectures_per_week, unavailable_days: []}]
    timings: list[TimingSlot]


@app.post("/api/timetable/generate")
def generate_timetable(req: TimetableGenerateRequest, institute: CurrentInstitute = Depends(require_write_access)):
    """Generate one batch timetable while deliberately spreading each teacher's
    weekly lectures across the week whenever the constraints allow it.

    Preferred weekdays are evenly spaced (for example Mon/Wed/Fri for three
    lectures), then batch, teacher and room conflicts are checked.
    """
    check_module_access(institute, "timetables")
    verify_branch_ownership(req.branch_id, institute.id)
    if not req.timings:
        raise HTTPException(status_code=400, detail="Add at least one lecture timing")

    conn = get_conn()
    cursor = conn.cursor()

    # Regenerate exactly this batch; other batches remain available for teacher
    # and room conflict checks.
    cursor.execute(
        "DELETE FROM timetables_slots WHERE branch_id = ? AND batch_name = ?",
        (req.branch_id, req.batch_name),
    )

    cursor.execute("SELECT room_no FROM classrooms WHERE branch_id = ? ORDER BY id", (req.branch_id,))
    available_rooms = [row[0] for row in cursor.fetchall() if row[0]]

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    day_index = {day: i for i, day in enumerate(days)}
    timings_sorted = sorted(req.timings, key=lambda t: t.lecture_number)
    generated_slots = []
    warnings = []

    # Current batch load is kept in memory as well as in SQLite so scoring is cheap.
    batch_load = {day: 0 for day in days}

    def slot_is_free(day, timing, teacher_name):
        slot_time = timing.time_slot
        cursor.execute(
            """SELECT COUNT(*) FROM timetables_slots
               WHERE branch_id = ? AND batch_name = ? AND day = ? AND time_slot = ?""",
            (req.branch_id, req.batch_name, day, slot_time),
        )
        if cursor.fetchone()[0] > 0:
            return False
        cursor.execute(
            """SELECT COUNT(*) FROM timetables_slots
               WHERE branch_id = ? AND day = ? AND time_slot = ? AND teacher = ?""",
            (req.branch_id, day, slot_time, teacher_name),
        )
        return cursor.fetchone()[0] == 0

    def free_room(day, slot_time):
        for candidate_room in available_rooms:
            cursor.execute(
                """SELECT COUNT(*) FROM timetables_slots
                   WHERE branch_id = ? AND day = ? AND time_slot = ? AND room = ?""",
                (req.branch_id, day, slot_time, candidate_room),
            )
            if cursor.fetchone()[0] == 0:
                return candidate_room
        return "Unassigned (no free classroom)" if available_rooms else "Unassigned (add a classroom)"

    for t_config in req.teachers_config:
        teacher_name = str(t_config.get('name', '')).strip()
        subject = str(t_config.get('subject', '')).strip()
        target_lectures = max(0, int(t_config.get('lectures_per_week', 0)))
        unavailable = {str(d).strip() for d in t_config.get('unavailable_days', [])}

        if not teacher_name or target_lectures == 0:
            continue

        assigned_count = 0
        used_days = []
        eligible_days = [d for d in days if d not in unavailable]

        # Evenly distribute the requested weekly lectures: 2 -> Mon/Fri,
        # 3 -> Mon/Wed/Fri, 4 -> Mon/Tue/Thu/Fri, 5 -> every weekday.
        if target_lectures <= 1:
            preferred_day_indices = [0] if eligible_days else []
        elif target_lectures <= len(eligible_days):
            preferred_day_indices = [
                round(i * (len(eligible_days) - 1) / (target_lectures - 1))
                for i in range(target_lectures)
            ]
        else:
            preferred_day_indices = [i % len(eligible_days) for i in range(target_lectures)] if eligible_days else []

        # Each lecture is chosen from ALL free day/time candidates. The scoring
        # strongly prefers the next evenly-spaced weekday, then an unused day,
        # then a lightly loaded day/time. Conflicts can still force a fallback.
        for lecture_index in range(target_lectures):
            candidates = []
            desired_idx = preferred_day_indices[lecture_index] if preferred_day_indices else 0
            desired_day = eligible_days[desired_idx] if eligible_days else None
            for day in days:
                if day in unavailable:
                    continue
                for timing in timings_sorted:
                    if not slot_is_free(day, timing, teacher_name):
                        continue
                    room = free_room(day, timing.time_slot)
                    idx = day_index[day]
                    min_distance = min((abs(idx - used) for used in used_days), default=5)
                    same_day_penalty = 1000 if idx in used_days and len(set(used_days)) < len(eligible_days) else 0
                    preferred_distance = abs(idx - day_index[desired_day]) if desired_day else 0
                    # Lower score wins. Preferred weekdays dominate, while
                    # day load and distance provide sensible tie-breaking.
                    score = (
                        preferred_distance * 100
                        + same_day_penalty
                        + batch_load[day] * 25
                        - min_distance * 2
                        + idx * 0.01
                        + timing.lecture_number * 0.001
                    )
                    candidates.append((score, day, timing, room))

            if not candidates:
                break

            _, day, timing, room = min(candidates, key=lambda x: x[0])
            cursor.execute(
                """INSERT INTO timetables_slots
                   (branch_id, batch_name, day, time_slot, lecture_number, subject, teacher, room)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (req.branch_id, req.batch_name, day, timing.time_slot,
                 timing.lecture_number, subject, teacher_name, room),
            )
            generated_slots.append({
                "day": day, "time_slot": timing.time_slot,
                "lecture_number": timing.lecture_number,
                "subject": subject, "teacher": teacher_name, "room": room,
            })
            assigned_count += 1
            batch_load[day] += 1
            used_days.append(day_index[day])
            teacher_days.append(day)

        if assigned_count < target_lectures:
            warnings.append(
                f"{teacher_name}: only scheduled {assigned_count}/{target_lectures} lectures "
                f"(not enough free day/time slots without a conflict)."
            )

    cursor.execute(
        """INSERT INTO timetable_configs
           (branch_id, batch_name, timings_json, teachers_config_json, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(branch_id, batch_name) DO UPDATE SET
               timings_json = excluded.timings_json,
               teachers_config_json = excluded.teachers_config_json,
               updated_at = excluded.updated_at""",
        (req.branch_id, req.batch_name,
         json.dumps([t.dict() for t in req.timings]),
         json.dumps(req.teachers_config), datetime.utcnow().isoformat()),
    )

    conn.commit()
    conn.close()
    return {"status": "success", "slots": generated_slots, "warnings": warnings}


@app.delete("/api/timetable/all/{branch_id}")
def delete_all_timetables(branch_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    """Completely reset the timetable workspace for the selected branch."""
    check_module_access(institute, "timetables")
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM timetables_slots WHERE branch_id = ?", (branch_id,))
    slots_deleted = cursor.rowcount
    cursor.execute("DELETE FROM timetable_configs WHERE branch_id = ?", (branch_id,))
    configs_deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return {"status": "cleared", "slots_deleted": slots_deleted, "configs_deleted": configs_deleted}


class TimetableSlotEdit(BaseModel):
    day: str
    time_slot: str
    subject: str
    teacher: str
    room: str


@app.patch("/api/timetable/slots/{slot_id}")
def edit_timetable_slot(slot_id: int, req: TimetableSlotEdit, institute: CurrentInstitute = Depends(require_write_access)):
    """Manual one-off override for a single generated slot (e.g. swapping a
    room or teacher by hand) - no conflict-checking, since the user is
    deliberately overriding the auto-generated result."""
    check_module_access(institute, "timetables")
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT timetables_slots.id FROM timetables_slots
           JOIN branches ON branches.id = timetables_slots.branch_id
           WHERE timetables_slots.id = ? AND branches.institute_id = ?""",
        (slot_id, institute.id),
    )
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Slot not found")
    cursor.execute(
        "UPDATE timetables_slots SET day = ?, time_slot = ?, subject = ?, teacher = ?, room = ? WHERE id = ?",
        (req.day, req.time_slot, req.subject, req.teacher, req.room, slot_id),
    )
    conn.commit()
    conn.close()
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# Exam seating
# ---------------------------------------------------------------------------

class SeatingGenerateRequest(BaseModel):
    branch_id: int
    exam_date: str
    room_number: str
    rows: int
    columns: int


def _build_seating_layout(students, rows, columns):
    """Assign students to a grid so orthogonally adjacent seats never share a
    batch. Uses multiple deterministic greedy restarts and rejects impossible
    layouts instead of silently violating the rule."""
    from collections import Counter

    capacity = rows * columns
    if len(students) > capacity:
        raise HTTPException(
            status_code=400,
            detail=f"Room capacity is {capacity}, but {len(students)} students were selected.",
        )
    if not students:
        raise HTTPException(status_code=400, detail="No students found in this branch.")

    batches = Counter((s["batch"] or "Unassigned").strip() or "Unassigned" for s in students)
    if max(batches.values()) > (capacity + 1) // 2:
        raise HTTPException(
            status_code=400,
            detail="A valid no-adjacent layout is impossible because one batch contains too many students for this room. Increase the room size or use a different exam room.",
        )

    # Row-major cells; each cell only constrains left and above neighbors.
    cells = [(r, c) for r in range(rows) for c in range(columns)]
    batch_names = sorted(batches, key=lambda b: (-batches[b], b.lower()))

    for attempt in range(120):
        remaining = dict(batches)
        placed_batches = {}
        failed = False

        # Mild deterministic variation between attempts helps with awkward
        # batch-size combinations without making the result nondeterministic.
        rotation = attempt % max(1, len(batch_names))
        priority_order = batch_names[rotation:] + batch_names[:rotation]

        # Only the first N cells need students; remaining room capacity stays empty.
        cells_to_fill = cells[:len(students)]
        for r, c in cells_to_fill:
            forbidden = set()
            if c > 0:
                forbidden.add(placed_batches[(r, c - 1)])
            if r > 0:
                forbidden.add(placed_batches[(r - 1, c)])

            candidates = [b for b in priority_order if remaining[b] > 0 and b not in forbidden]
            if not candidates:
                failed = True
                break

            # Largest remaining batch first prevents a dominant batch from
            # being stranded at the end; a small positional tie-break varies
            # across attempts.
            candidates.sort(key=lambda b: (-remaining[b], priority_order.index(b)))
            chosen = candidates[0]
            placed_batches[(r, c)] = chosen
            remaining[chosen] -= 1

        if not failed:
            by_batch = {b: [] for b in batches}
            for student in students:
                by_batch[(student["batch"] or "Unassigned").strip() or "Unassigned"].append(student)
            for b in by_batch:
                by_batch[b].sort(key=lambda x: (x.get("roll_number") or "", x.get("name") or ""))

            assignments = []
            counters = {b: 0 for b in by_batch}
            for r, c in cells_to_fill:
                b = placed_batches.get((r, c))
                if not b:
                    continue
                student = by_batch[b][counters[b]]
                counters[b] += 1
                assignments.append({
                    "row": r + 1,
                    "column": c + 1,
                    "student_id": student["id"],
                    "name": student["name"],
                    "batch": b,
                    "roll_number": student["roll_number"],
                })
            return assignments

    raise HTTPException(
        status_code=400,
        detail="Could not find a valid seating arrangement for these batch sizes and room dimensions. Try a larger room or different room dimensions.",
    )


@app.get("/api/seating/{branch_id}")
def get_seating_layouts(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, SEATING_MODULE)
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn(); conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, exam_date, room_number, rows, columns, assignments_json, created_at FROM exam_seatings WHERE branch_id = ? ORDER BY exam_date DESC, id DESC",
        (branch_id,),
    ).fetchall()
    conn.close()
    return [{**dict(r), "assignments": json.loads(r["assignments_json"])} for r in rows]


@app.post("/api/seating/generate")
def generate_seating(req: SeatingGenerateRequest, institute: CurrentInstitute = Depends(require_write_access)):
    check_module_access(institute, SEATING_MODULE)
    verify_branch_ownership(req.branch_id, institute.id)
    if req.rows < 1 or req.columns < 1:
        raise HTTPException(status_code=400, detail="Rows and columns must both be at least 1.")
    room_number = req.room_number.strip()
    if not room_number:
        raise HTTPException(status_code=400, detail="Room number is required.")

    conn = get_conn(); conn.row_factory = sqlite3.Row
    students = conn.execute(
        """SELECT id, COALESCE(full_name, name) AS name, COALESCE(batch, '') AS batch, COALESCE(roll_number, '') AS roll_number
           FROM students WHERE branch_id = ? ORDER BY LOWER(COALESCE(batch, '')), LOWER(COALESCE(full_name, name, ''))""",
        (req.branch_id,),
    ).fetchall()
    student_rows = [dict(r) for r in students]
    assignments = _build_seating_layout(student_rows, req.rows, req.columns)

    cursor = conn.cursor()
    cursor.execute(
        """DELETE FROM exam_seatings WHERE branch_id = ? AND exam_date = ? AND room_number = ?""",
        (req.branch_id, req.exam_date, room_number),
    )
    cursor.execute(
        """INSERT INTO exam_seatings (branch_id, exam_date, room_number, rows, columns, assignments_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (req.branch_id, req.exam_date, room_number, req.rows, req.columns,
         json.dumps(assignments), datetime.utcnow().isoformat()),
    )
    layout_id = cursor.lastrowid
    conn.commit(); conn.close()
    return {"status": "success", "id": layout_id, "assignments": assignments}


@app.delete("/api/seating/{layout_id}")
def delete_seating_layout(layout_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    check_module_access(institute, SEATING_MODULE)
    conn = get_conn(); cursor = conn.cursor()
    cursor.execute(
        """SELECT exam_seatings.id FROM exam_seatings JOIN branches ON branches.id = exam_seatings.branch_id
           WHERE exam_seatings.id = ? AND branches.institute_id = ?""",
        (layout_id, institute.id),
    )
    if not cursor.fetchone():
        conn.close(); raise HTTPException(status_code=404, detail="Seating layout not found")
    cursor.execute("DELETE FROM exam_seatings WHERE id = ?", (layout_id,))
    conn.commit(); conn.close()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Dashboard analytics
# ---------------------------------------------------------------------------

IST_OFFSET = timedelta(hours=5, minutes=30)


def _parse_time_range(time_slot: str):
    """Best-effort parse of a free-text '09:00 AM - 10:00 AM' timing string
    into two time objects. Returns (None, None) if it doesn't match - a
    malformed timing just never counts as 'ongoing', it doesn't crash."""
    import re
    m = re.match(r"\s*(\d{1,2}:\d{2}\s*[AaPp][Mm])\s*-\s*(\d{1,2}:\d{2}\s*[AaPp][Mm])\s*", time_slot or "")
    if not m:
        return None, None
    try:
        start = datetime.strptime(m.group(1).upper().replace(" ", ""), "%I:%M%p").time()
        end = datetime.strptime(m.group(2).upper().replace(" ", ""), "%I:%M%p").time()
        return start, end
    except ValueError:
        return None, None


@app.get("/api/dashboard/{branch_id}")
def get_dashboard(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    verify_branch_ownership(branch_id, institute.id)
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    now_ist = datetime.utcnow() + IST_OFFSET
    today = now_ist.date()
    week_start = today - timedelta(days=6)  # last 7 days including today

    # --- Attendance this week, per batch ---
    attendance_week = []
    if institute.is_owner or "attendance" in institute.allowed_modules:
        cursor.execute("SELECT id, name, batch FROM students WHERE branch_id = ?", (branch_id,))
        student_batch = {row["name"]: (row["batch"] or "Unassigned") for row in cursor.fetchall()}
        cursor.execute(
            "SELECT student_name, status FROM attendance WHERE branch_id = ? AND date >= ? AND date <= ?",
            (branch_id, week_start.isoformat(), today.isoformat()),
        )
        per_batch = {}
        for row in cursor.fetchall():
            batch = student_batch.get(row["student_name"], "Unassigned")
            b = per_batch.setdefault(batch, {"present": 0, "total": 0})
            b["total"] += 1
            if row["status"] == "Present":
                b["present"] += 1
        for batch, stats in sorted(per_batch.items()):
            pct = round(100 * stats["present"] / stats["total"]) if stats["total"] else 0
            attendance_week.append({"batch": batch, "present": stats["present"], "total": stats["total"], "pct": pct})

    # --- Fees pending ---
    fees_pending_total, fees_pending_count = 0, 0
    if institute.is_owner or "fees" in institute.allowed_modules:
        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_inr), 0) FROM fees WHERE branch_id = ? AND LOWER(COALESCE(status, '')) != 'paid'",
            (branch_id,),
        )
        fees_pending_count, fees_pending_total = cursor.fetchone()

    # --- Lectures ongoing right now, per batch ---
    ongoing_lectures = []
    if institute.is_owner or "timetables" in institute.allowed_modules:
        today_name = now_ist.strftime("%A")
        now_time = now_ist.time()
        cursor.execute(
            "SELECT batch_name, day, time_slot, subject, teacher, room FROM timetables_slots WHERE branch_id = ? AND day = ?",
            (branch_id, today_name),
        )
        for row in cursor.fetchall():
            start, end = _parse_time_range(row["time_slot"])
            if start and end and start <= now_time <= end:
                ongoing_lectures.append({
                    "batch_name": row["batch_name"], "time_slot": row["time_slot"],
                    "subject": row["subject"], "teacher": row["teacher"], "room": row["room"],
                })

    conn.close()
    return {
        "attendance_week": attendance_week,
        "fees_pending_total": fees_pending_total,
        "fees_pending_count": fees_pending_count,
        "ongoing_lectures": ongoing_lectures,
        "as_of": now_ist.isoformat(),
    }


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def read_root():
    return HTMLResponse(content=HTML_CONTENT, status_code=200)


HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALGORITHMIC - Enterprise Institutional Operations</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.5.25/jspdf.plugin.autotable.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700;9..144,900&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Playfair+Display:ital,wght@0,700;0,800;0,900;1,700&display=swap');

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

        /* Heavier, higher-contrast display serif reserved for the institute name and the homepage welcome line - bolder and more formal than the base Fraunces headings */
        .premium-heading-font { font-family: 'Playfair Display', serif; font-weight: 800; letter-spacing: -0.01em; }

        /* Bold, all-caps "statement" font for the institute name and welcome
           line - heavier and blockier than premium-heading-font, on brand
           with the "not a website, a statement" direction. */
        .command-heading-font { font-family: "Footlight MT Light", "Footlight MT", "Times New Roman", serif; font-weight: 300; letter-spacing: 0.01em; }

        .mini-bar-track { background: rgba(212,175,55,0.08); border-radius: 999px; overflow: hidden; height: 8px; }
        .mini-bar-fill { background: linear-gradient(90deg, #8a6a22, #E8C767); height: 100%; border-radius: 999px; transition: width 0.4s ease; }
        .module-check { accent-color: #D4AF37; }
        .ongoing-dot { width: 7px; height: 7px; border-radius: 999px; background: #4ade80; box-shadow: 0 0 6px rgba(74,222,128,0.7); display: inline-block; }

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

        @keyframes welcomeFadeSlide {
            0% { opacity: 0; transform: translateY(16px); }
            100% { opacity: 1; transform: translateY(0); }
        }
        .welcome-animate { animation: welcomeFadeSlide 0.9s cubic-bezier(0.22, 1, 0.36, 1); }

        .batch-group-row td { background: rgba(212, 175, 55, 0.06); }
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
                    <h1 id="headerInstituteName" onclick="openRenameInstituteModal()" title="Click to rename your institute" class="editable-name command-heading-font text-3xl gold-gradient-text tracking-tight leading-none">—</h1>
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
                <button id="navStudents" data-module="students" onclick="switchModule('students')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🎓</span><span>Students</span></button>
                <button id="navTeachers" data-module="teachers" onclick="switchModule('teachers')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>👨‍🏫</span><span>Teachers</span></button>
                <button id="navClassrooms" data-module="classrooms" onclick="switchModule('classrooms')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🏛️</span><span>Classrooms</span></button>
                <button id="navSyllabus" data-module="syllabus" onclick="switchModule('syllabus')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>📚</span><span>Syllabus</span></button>
                <button id="navAttendance" data-module="attendance" onclick="switchModule('attendance')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>📋</span><span>Attendance</span></button>
                <button id="navTimetables" data-module="timetables" onclick="switchModule('timetables')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🕒</span><span>Timetable</span></button>
                <button id="navSeating" data-module="seating" onclick="switchModule('seating')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🪑</span><span>Exam Seating</span></button>
                <button id="navInvigilation" data-module="invigilation" onclick="switchModule('invigilation')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>🛡️</span><span>Invigilator Duty</span></button>
                <button id="navFees" data-module="fees" onclick="switchModule('fees')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>💳</span><span>Fees (INR ₹)</span></button>
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
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-lg shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 id="userModalTitle" class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Add User</h3>
                <button onclick="closeUserModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <div class="space-y-4">
                <input type="text" id="newUserName" placeholder="Full Name" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <input type="email" id="newUserEmail" placeholder="Email Address" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <input type="password" id="newUserPassword" placeholder="Password (min 8 characters)" minlength="8" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Designation</label>
                    <input type="text" id="newUserDesignation" list="designationPresets" placeholder="e.g. Admin, Accountant, Teacher, Head" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                    <datalist id="designationPresets">
                        <option value="Admin"><option value="Accountant"><option value="Teacher"><option value="Head"><option value="Clerk">
                    </datalist>
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Permission</label>
                    <select id="newUserPermission" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                        <option value="edit">Edit Access</option>
                        <option value="read_only">Read Only</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Module Access <span class="text-gray-600 normal-case font-normal">(only the boss sees everything)</span></label>
                    <div id="newUserModuleGrid" class="grid grid-cols-2 gap-2"></div>
                </div>
                <div id="userModalError" class="auth-error"></div>
                <button onclick="submitUserForm()" id="userModalSubmitBtn" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Create User</button>
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
                    <button type="submit" id="recordSubmitBtn" class="px-6 py-2.5 text-xs font-extrabold uppercase gold-bg hover:opacity-95 text-black rounded-xl fast-transition shadow-lg">Save Record</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Attendance History Modal -->
    <div id="attendanceHistoryModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-md shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 id="attendanceHistoryTitle" class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Attendance History</h3>
                <button onclick="closeAttendanceHistory()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <div id="attendanceHistoryBody"></div>
        </div>
    </div>

    <!-- Timetable Slot Edit Modal -->
    <div id="timetableSlotEditModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-md shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Edit Timetable Slot</h3>
                <button onclick="closeTimetableSlotEdit()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <form onsubmit="submitTimetableSlotEdit(event)" class="space-y-4">
                <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Day</label><input type="text" id="ttSlotEditDay" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Time Slot</label><input type="text" id="ttSlotEditTime" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Subject</label><input type="text" id="ttSlotEditSubject" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Teacher</label><input type="text" id="ttSlotEditTeacher" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Room</label><input type="text" id="ttSlotEditRoom" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                <p class="text-[11px] text-gray-500">Manual overrides aren't conflict-checked - you're taking the wheel on this one.</p>
                <div class="flex justify-end space-x-3 pt-4 border-t gold-border">
                    <button type="button" onclick="closeTimetableSlotEdit()" class="px-5 py-2.5 text-xs font-bold uppercase bg-gray-900 hover:bg-gray-800 text-gray-300 rounded-xl fast-transition">Cancel</button>
                    <button type="submit" class="px-6 py-2.5 text-xs font-extrabold uppercase gold-bg hover:opacity-95 text-black rounded-xl fast-transition shadow-lg">Save Changes</button>
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
        let myFullName = '';
        let myDesignation = 'Owner';
        let myAllowedModules = [];
        let bulkImportModule = null;
        const ALL_MODULES = ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'timetables', 'seating', 'invigilation', 'fees'];

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
            myFullName = data.full_name || data.institute_name || '';
            myDesignation = data.designation || (isOwner ? 'Owner' : 'Staff');
            myAllowedModules = isOwner ? ALL_MODULES.slice() : (data.allowed_modules || []);
            document.getElementById('headerInstituteName').textContent = data.institute_name;
            document.getElementById('headerFullName').textContent = data.full_name || data.institute_name;
            document.getElementById('navManageUsers').classList.toggle('hidden', !isOwner);
            ALL_MODULES.forEach(m => {
                const btn = document.querySelector(`[data-module="${m}"]`);
                if (btn) btn.classList.toggle('hidden', !isOwner && !myAllowedModules.includes(m));
            });
            const badge = document.getElementById('headerPermBadge');
            if (isOwner) { badge.innerHTML = `<span class="perm-badge perm-edit">Owner</span>`; }
            else {
                const accessBadge = myPermission === 'read_only'
                    ? '<span class="perm-badge perm-readonly">Read Only</span>'
                    : '<span class="perm-badge perm-edit">Edit Access</span>';
                badge.innerHTML = `<span class="perm-badge perm-readonly">${myDesignation}</span> ${accessBadge}`;
            }
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
            if (moduleName !== 'home' && moduleName !== 'users' && !isOwner && !myAllowedModules.includes(moduleName)) {
                alert('Your account does not have access to that module.');
                return;
            }
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
            } else if (currentModule === 'students') {
                await renderStudentsModule(container);
            } else if (currentModule === 'attendance') {
                await renderAttendanceModule(container);
            } else if (currentModule === 'seating') {
                await renderSeatingModule(container);
            } else {
                await renderDataModule(container, currentModule);
            }
        }

        function renderHomeModule(container) {
            const can = (m) => isOwner || myAllowedModules.includes(m);
            container.innerHTML = `
                <div class="space-y-8">
                    <div class="glass-panel border gold-border p-10 rounded-3xl relative overflow-hidden shadow-2xl">
                        <div class="max-w-3xl relative z-10 space-y-4">
                            <span class="text-xs uppercase tracking-widest px-3 py-1 rounded-full bg-[#1c1c1c] gold-gradient-text border gold-border font-extrabold">Executive Command Center</span>
                            <h2 class="welcome-animate command-heading-font text-5xl gold-gradient-text tracking-tight leading-tight">Welcome, ${myFullName || 'there'}</h2>
                        </div>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
                        ${can('students') ? `<div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Active Students</div><div class="text-3xl font-black gold-gradient-text" id="statStudents">—</div></div>` : ''}
                        ${can('teachers') ? `<div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Faculty Members</div><div class="text-3xl font-black gold-gradient-text" id="statTeachers">—</div></div>` : ''}
                        ${can('classrooms') ? `<div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Classrooms Available</div><div class="text-3xl font-black gold-gradient-text" id="statClassrooms">—</div></div>` : ''}
                        ${can('fees') ? `<div class="glass-panel p-6 rounded-2xl border gold-border"><div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Fees Pending</div><div class="text-3xl font-black gold-gradient-text" id="statFeesPending">—</div></div>` : ''}
                    </div>
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        ${can('attendance') ? `
                        <div class="glass-panel p-6 rounded-2xl border gold-border">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider mb-4">Attendance This Week · Per Batch</h3>
                            <div id="dashAttendanceChart" class="space-y-3"><p class="text-xs text-gray-500">Loading…</p></div>
                        </div>` : ''}
                        ${can('timetables') ? `
                        <div class="glass-panel p-6 rounded-2xl border gold-border">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider mb-4">Ongoing Lectures Right Now</h3>
                            <div id="dashOngoingLectures" class="space-y-2"><p class="text-xs text-gray-500">Loading…</p></div>
                        </div>` : ''}
                    </div>
                </div>
            `;
            loadHomeStats();
        }

        async function loadHomeStats() {
            try {
                if (!currentBranchId) return;
                const can = (m) => isOwner || myAllowedModules.includes(m);

                if (can('students')) {
                    const r = await authFetch(`/api/records/students/${currentBranchId}`);
                    document.getElementById('statStudents').textContent = (await r.json()).length;
                }
                if (can('teachers')) {
                    const r = await authFetch(`/api/records/teachers/${currentBranchId}`);
                    document.getElementById('statTeachers').textContent = (await r.json()).length;
                }
                if (can('classrooms')) {
                    const r = await authFetch(`/api/records/classrooms/${currentBranchId}`);
                    document.getElementById('statClassrooms').textContent = (await r.json()).length;
                }

                const dRes = await authFetch(`/api/dashboard/${currentBranchId}`);
                const dash = await dRes.json();

                if (can('fees')) {
                    document.getElementById('statFeesPending').textContent = `₹${(dash.fees_pending_total || 0).toLocaleString('en-IN')}`;
                }

                if (can('attendance')) {
                    const el = document.getElementById('dashAttendanceChart');
                    if (!dash.attendance_week.length) {
                        el.innerHTML = '<p class="text-xs text-gray-500">No attendance marked in the last 7 days.</p>';
                    } else {
                        el.innerHTML = dash.attendance_week.map(b => `
                            <div>
                                <div class="flex justify-between text-xs mb-1"><span class="text-gray-300 font-semibold">${esc(b.batch)}</span><span class="text-gray-500">${b.present}/${b.total} present · ${b.pct}%</span></div>
                                <div class="mini-bar-track"><div class="mini-bar-fill" style="width:${b.pct}%"></div></div>
                            </div>
                        `).join('');
                    }
                }

                if (can('timetables')) {
                    const el = document.getElementById('dashOngoingLectures');
                    if (!dash.ongoing_lectures.length) {
                        el.innerHTML = '<p class="text-xs text-gray-500">No lecture is currently in session.</p>';
                    } else {
                        el.innerHTML = dash.ongoing_lectures.map(l => `
                            <div class="flex items-center justify-between text-xs bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3">
                                <div class="flex items-center space-x-2"><span class="ongoing-dot"></span><span class="font-semibold text-white">${esc(l.batch_name)}</span><span class="text-gray-500">${esc(l.subject)}</span></div>
                                <div class="text-gray-400">${esc(l.teacher)} · ${esc(l.room)}</div>
                            </div>
                        `).join('');
                    }
                }
            } catch (e) { console.error(e); }
        }

        // Small HTML-escaping helper reused across dashboard/table rendering.
        function esc(v) { return String(v ?? '').replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c])); }

        // Shared by the on-screen table AND both export functions, so a
        // module's columns/order never drift apart between what's shown and
        // what's exported.
        function getModuleColumns(moduleName, records) {
            const hiddenKeys = ['id', 'branch_id', 'building'];
            return MODULE_COLUMNS[moduleName] ||
                (records[0] ? Object.keys(records[0]).filter(k => !hiddenKeys.includes(k)).map(k => ({ key: k, label: k.replace('_', ' ') })) : []);
        }

        function formatExportValue(moduleName, key, val) {
            if (moduleName === 'fees' && key === 'amount_inr') return `Rs. ${parseFloat(val || 0).toLocaleString('en-IN')}`;
            if (key === 'document') return val ? 'Attached' : 'No File';
            return val ?? '';
        }

        function exportModulePDF(moduleName) {
            const records = moduleRecordsCache[moduleName] || [];
            if (!records.length) { alert('Nothing to export yet.'); return; }
            const columns = getModuleColumns(moduleName, records);
            const doc = new window.jspdf.jsPDF();
            doc.setFontSize(14);
            doc.text(`${moduleName[0].toUpperCase()}${moduleName.slice(1)} - Algorithmic`, 14, 16);
            doc.autoTable({
                startY: 22,
                head: [columns.map(c => c.label)],
                body: records.map(r => columns.map(c => String(formatExportValue(moduleName, c.key, r[c.key])))),
                styles: { fontSize: 8 },
                headStyles: { fillColor: [20, 20, 20] },
            });
            doc.save(`${moduleName}_export.pdf`);
        }

        function exportModuleExcel(moduleName) {
            const records = moduleRecordsCache[moduleName] || [];
            if (!records.length) { alert('Nothing to export yet.'); return; }
            const columns = getModuleColumns(moduleName, records);
            const rows = records.map(r => {
                const row = {};
                columns.forEach(c => { row[c.label] = formatExportValue(moduleName, c.key, r[c.key]); });
                return row;
            });
            const ws = window.XLSX.utils.json_to_sheet(rows);
            const wb = window.XLSX.utils.book_new();
            window.XLSX.utils.book_append_sheet(wb, ws, moduleName.slice(0, 31));
            window.XLSX.writeFile(wb, `${moduleName}_export.xlsx`);
        }

        async function renderDataModule(container, moduleName) {
            const canWrite = myPermission !== 'read_only';
            container.innerHTML = `
                <div class="space-y-6">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">${moduleName} Department</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Branch Synchronized</p>
                        </div>
                        ${canWrite ? `
                        <div class="flex items-center space-x-3">
                            <button onclick="openRecordModal('${moduleName}')" class="gold-bg hover:opacity-95 text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add New Record</button>
                            <button onclick="openBulkImportModal('${moduleName}')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add Document</button>
                        </div>` : ''}
                    </div>
                    <div class="flex items-center justify-end space-x-3">
                        <button onclick="exportModulePDF('${moduleName}')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-bold px-4 py-2 rounded-lg text-xs uppercase tracking-wider fast-transition">Export PDF</button>
                        <button onclick="exportModuleExcel('${moduleName}')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-bold px-4 py-2 rounded-lg text-xs uppercase tracking-wider fast-transition">Export Excel</button>
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

        // Explicit column order/labels for modules with a fixed, curated column set.
        // Modules not listed here fall back to showing every DB column returned.
        const MODULE_COLUMNS = {
            students: [
                { key: 'name', label: 'Name' },
                { key: 'batch', label: 'Batch' },
                { key: 'roll_number', label: 'Roll Number' },
                { key: 'parent_contact', label: "Parent's Contact Number" },
            ],
            teachers: [
                { key: 'name', label: 'Name' },
                { key: 'subject', label: 'Subject' },
                { key: 'contact_number', label: 'Contact Number' },
            ],
            syllabus: [
                { key: 'subject', label: 'Subject' },
                { key: 'topic', label: 'Topic' },
                { key: 'teacher_name', label: "Teacher's Name" },
                { key: 'num_lectures', label: 'Number of Lectures' },
                { key: 'lecture_date', label: 'Date' },
            ],
            classrooms: [
                { key: 'room_no', label: 'Room Number' },
                { key: 'capacity', label: 'Seating Capacity' },
            ],
            invigilation: [
                { key: 'teacher_name', label: 'Teacher Name' },
                { key: 'exam_date', label: 'Exam Date' },
                { key: 'room', label: 'Exam Hall' },
            ],
            fees: [
                { key: 'student_name', label: 'Student Name' },
                { key: 'amount_inr', label: 'Amount (INR)' },
                { key: 'status', label: 'Status' },
                { key: 'due_date', label: 'Due Date' },
            ],
        };

        let moduleRecordsCache = {};

        async function loadModuleRecords(moduleName) {
            if (!currentBranchId) return;
            const canWrite = myPermission !== 'read_only';
            const res = await authFetch(`/api/records/${moduleName}/${currentBranchId}`);
            const records = await res.json();
            moduleRecordsCache[moduleName] = records;
            const thead = document.getElementById('moduleTableHead');
            const tbody = document.getElementById('moduleTableBody');

            if (records.length === 0) {
                thead.innerHTML = `<tr><th class="p-4">Status</th></tr>`;
                tbody.innerHTML = `<tr><td class="p-8 text-center text-gray-500">No records found for ${moduleName}. ${canWrite ? "Click '+ Add New Record' to create one." : ''}</td></tr>`;
                return;
            }

            // 'building' is retired from classrooms, and record ownership fields never render.
            const columns = getModuleColumns(moduleName, records);

            thead.innerHTML = `<tr>${columns.map(c => `<th class="p-4 uppercase tracking-wider text-xs font-bold">${c.label}</th>`).join('')}${canWrite ? '<th class="p-4"></th>' : ''}</tr>`;
            tbody.innerHTML = records.map(r => `
                <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                    ${columns.map(c => {
                        let val = r[c.key];
                        if (moduleName === 'fees' && c.key === 'amount_inr') { val = `₹${parseFloat(val || 0).toLocaleString('en-IN')}`; }
                        if (c.key === 'document' && val) { val = `<a href="/uploads/${val}" target="_blank" class="text-yellow-500 underline text-xs font-semibold">View File</a>`; }
                        else if (c.key === 'document' && !val) { val = `<span class="text-gray-600 text-xs">No File</span>`; }
                        return `<td class="p-4 font-medium">${val ?? ''}</td>`;
                    }).join('')}
                    ${canWrite ? `<td class="p-4 text-right whitespace-nowrap"><button onclick="openRecordModal('${moduleName}', ${r.id})" title="Edit record" class="row-delete-btn fast-transition text-sm leading-none mr-3">✎</button><button onclick="deleteRecord('${moduleName}', ${r.id})" title="Delete record" class="row-delete-btn fast-transition text-lg leading-none">🗑</button></td>` : ''}
                </tr>
            `).join('');
        }

        // ---- Student Department (batchwise segregation + search) ----

        let allStudentRecords = [];

        async function renderStudentsModule(container) {
            const canWrite = myPermission !== 'read_only';
            container.innerHTML = `
                <div class="space-y-6">
                    <div class="flex justify-between items-center flex-wrap gap-4">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Student Department</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Segregated Batchwise • A-Z</p>
                        </div>
                        ${canWrite ? `
                        <div class="flex items-center space-x-3">
                            <button onclick="openRecordModal('students')" class="gold-bg hover:opacity-95 text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add New Record</button>
                            <button onclick="openBulkImportModal('students')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">+ Add Document</button>
                        </div>` : ''}
                    </div>
                    <div class="flex items-center justify-end space-x-3">
                        <button onclick="exportModulePDF('students')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-bold px-4 py-2 rounded-lg text-xs uppercase tracking-wider fast-transition">Export PDF</button>
                        <button onclick="exportModuleExcel('students')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-bold px-4 py-2 rounded-lg text-xs uppercase tracking-wider fast-transition">Export Excel</button>
                    </div>
                    <div class="glass-panel border gold-border rounded-2xl p-6 shadow-2xl">
                        <input type="text" id="studentSearchInput" placeholder="Search student by name..." class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none mb-4">
                        <div class="overflow-x-auto">
                            <table class="w-full text-left text-sm text-gray-300">
                                <thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border">
                                    <tr>
                                        <th class="p-4 font-bold tracking-wider">Name</th>
                                        <th class="p-4 font-bold tracking-wider">Batch</th>
                                        <th class="p-4 font-bold tracking-wider">Roll Number</th>
                                        <th class="p-4 font-bold tracking-wider">Parent's Contact Number</th>
                                        ${canWrite ? '<th class="p-4"></th>' : ''}
                                    </tr>
                                </thead>
                                <tbody id="studentTableBody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
            document.getElementById('studentSearchInput').addEventListener('input', renderStudentTable);
            await loadStudentRecords();
        }

        async function loadStudentRecords() {
            if (!currentBranchId) return;
            const res = await authFetch(`/api/records/students/${currentBranchId}`);
            allStudentRecords = await res.json();
            moduleRecordsCache['students'] = allStudentRecords;
            renderStudentTable();
        }

        function renderStudentTable() {
            const canWrite = myPermission !== 'read_only';
            const tbody = document.getElementById('studentTableBody');
            const query = (document.getElementById('studentSearchInput')?.value || '').trim().toLowerCase();

            let students = allStudentRecords.slice();
            if (query) students = students.filter(s => (s.name || '').toLowerCase().includes(query));

            // Segregate batchwise: alphabetical by batch name, then alphabetical by student name within each batch.
            students.sort((a, b) => {
                const batchA = (a.batch || '').trim(), batchB = (b.batch || '').trim();
                if (batchA !== batchB) return batchA.localeCompare(batchB);
                return (a.name || '').localeCompare(b.name || '');
            });

            if (students.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="p-8 text-center text-gray-500">No students found.</td></tr>`;
                return;
            }

            let rows = '';
            let lastBatch = null;
            students.forEach(s => {
                const batch = (s.batch || '').trim() || 'Unassigned';
                if (batch !== lastBatch) {
                    rows += `<tr class="batch-group-row"><td colspan="${canWrite ? 5 : 4}" class="p-2.5 text-xs font-extrabold uppercase tracking-widest gold-gradient-text">${batch}</td></tr>`;
                    lastBatch = batch;
                }
                rows += `
                    <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                        <td class="p-4 font-medium">${s.name ?? ''}</td>
                        <td class="p-4">${s.batch ?? ''}</td>
                        <td class="p-4">${s.roll_number ?? ''}</td>
                        <td class="p-4">${s.parent_contact ?? ''}</td>
                        ${canWrite ? `<td class="p-4 text-right whitespace-nowrap"><button onclick="openRecordModal('students', ${s.id})" title="Edit record" class="row-delete-btn fast-transition text-sm leading-none mr-3">✎</button><button onclick="deleteRecord('students', ${s.id})" title="Delete record" class="row-delete-btn fast-transition text-lg leading-none">🗑</button></td>` : ''}
                    </tr>`;
            });
            tbody.innerHTML = rows;
        }

        // ---- Attendance (batchwise, sourced live from Student Department) ----

        let attendanceAllStudents = [];
        let attendanceMarksToday = {};
        const attendanceToday = new Date().toISOString().slice(0, 10);

        async function renderAttendanceModule(container) {
            container.innerHTML = `
                <div class="space-y-6">
                    <div>
                        <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Attendance</h2>
                        <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Batchwise • ${attendanceToday}</p>
                    </div>
                    <div class="glass-panel border gold-border rounded-2xl p-6 shadow-2xl space-y-5">
                        <div class="flex flex-wrap items-end gap-4">
                            <div>
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Batch</label>
                                <select id="attendanceBatchSelect" class="bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none min-w-[200px]"></select>
                            </div>
                            <div class="flex-1 min-w-[220px]">
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Search Student</label>
                                <input type="text" id="attendanceSearchInput" placeholder="Search by name..." class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                            </div>
                        </div>
                        <div id="attendanceStudentList" class="divide-y divide-gray-900"></div>
                    </div>
                </div>
            `;
            await loadAttendanceBatches();
            document.getElementById('attendanceBatchSelect').addEventListener('change', renderAttendanceStudentList);
            document.getElementById('attendanceSearchInput').addEventListener('input', renderAttendanceStudentList);
        }

        async function loadAttendanceBatches() {
            if (!currentBranchId) return;
            const [sRes, aRes] = await Promise.all([
                authFetch(`/api/records/students/${currentBranchId}`),
                authFetch(`/api/attendance/${currentBranchId}/${attendanceToday}`)
            ]);
            attendanceAllStudents = await sRes.json();
            attendanceMarksToday = await aRes.json();

            const batches = [...new Set(attendanceAllStudents.map(s => (s.batch || '').trim()).filter(Boolean))]
                .sort((a, b) => a.localeCompare(b));
            const select = document.getElementById('attendanceBatchSelect');
            select.innerHTML = batches.length === 0
                ? '<option value="">No batches found</option>'
                : batches.map(b => `<option value="${b}">${b}</option>`).join('');
            renderAttendanceStudentList();
        }

        function renderAttendanceStudentList() {
            const listEl = document.getElementById('attendanceStudentList');
            const canWrite = myPermission !== 'read_only';
            const selectedBatch = document.getElementById('attendanceBatchSelect').value;
            const query = document.getElementById('attendanceSearchInput').value.trim().toLowerCase();

            let students = attendanceAllStudents.filter(s => (s.batch || '').trim() === selectedBatch);
            if (query) students = students.filter(s => (s.name || '').toLowerCase().includes(query));
            students.sort((a, b) => (a.name || '').localeCompare(b.name || ''));

            if (students.length === 0) {
                listEl.innerHTML = `<p class="text-center text-gray-500 text-sm py-8">No students found${selectedBatch ? ` in ${selectedBatch}` : ''}.</p>`;
                return;
            }

            listEl.innerHTML = students.map(s => {
                const mark = attendanceMarksToday[s.name];
                const safeName = (s.name || '').replace(/'/g, "\\'");
                return `
                <div class="flex items-center justify-between py-3">
                    <span class="text-sm font-medium text-gray-200">${esc(s.name)}</span>
                    <div class="flex items-center space-x-2">
                        <button onclick="openAttendanceHistory('${safeName}')" title="View past attendance" class="px-3 py-1.5 rounded-lg text-xs font-extrabold uppercase tracking-wider fast-transition bg-[#141414] text-gray-400 border gold-border hover:text-yellow-500">History</button>
                        <button ${canWrite ? '' : 'disabled'} onclick="markAttendance('${safeName}', 'Present')" class="px-4 py-1.5 rounded-lg text-xs font-extrabold uppercase tracking-wider fast-transition ${mark === 'Present' ? 'bg-green-600 text-white' : 'bg-[#141414] text-gray-400 border gold-border hover:text-green-400'}">Present</button>
                        <button ${canWrite ? '' : 'disabled'} onclick="markAttendance('${safeName}', 'Absent')" class="px-4 py-1.5 rounded-lg text-xs font-extrabold uppercase tracking-wider fast-transition ${mark === 'Absent' ? 'bg-red-600 text-white' : 'bg-[#141414] text-gray-400 border gold-border hover:text-red-400'}">Absent</button>
                    </div>
                </div>`;
            }).join('');
        }

        async function openAttendanceHistory(studentName) {
            const modal = document.getElementById('attendanceHistoryModal');
            const body = document.getElementById('attendanceHistoryBody');
            document.getElementById('attendanceHistoryTitle').textContent = `Attendance History · ${studentName}`;
            body.innerHTML = '<p class="text-xs text-gray-500 p-4">Loading…</p>';
            modal.classList.remove('hidden');
            try {
                const res = await authFetch(`/api/attendance/history/${currentBranchId}?student_name=${encodeURIComponent(studentName)}`);
                const data = await res.json();
                if (!data.history.length) {
                    body.innerHTML = '<p class="text-xs text-gray-500 p-4">No attendance marked yet for this student.</p>';
                    return;
                }
                body.innerHTML = `
                    <div class="flex justify-between text-xs text-gray-400 px-1 pb-3 border-b gold-border mb-3">
                        <span>${data.total_marked} days marked</span>
                        <span class="text-green-400">${data.present_count} present</span>
                        <span class="text-red-400">${data.absent_count} absent</span>
                    </div>
                    <div class="max-h-72 overflow-y-auto divide-y divide-gray-900">
                        ${data.history.map(h => `
                            <div class="flex justify-between items-center py-2 text-sm">
                                <span class="text-gray-300">${esc(h.date)}</span>
                                <span class="font-semibold ${h.status === 'Present' ? 'text-green-400' : 'text-red-400'}">${esc(h.status)}</span>
                            </div>
                        `).join('')}
                    </div>
                `;
            } catch (e) {
                body.innerHTML = '<p class="text-xs text-red-400 p-4">Failed to load history.</p>';
            }
        }

        function closeAttendanceHistory() { document.getElementById('attendanceHistoryModal').classList.add('hidden'); }

        async function markAttendance(studentName, status) {
            const res = await authFetch('/api/attendance/mark', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ branch_id: currentBranchId, student_name: studentName, date: attendanceToday, status })
            });
            if (res.ok) {
                attendanceMarksToday[studentName] = status;
                renderAttendanceStudentList();
            } else {
                alert('Failed to mark attendance.');
            }
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
            students: ['name', 'batch', 'roll_number', 'parent_contact'],
            teachers: ['name', 'subject', 'contact_number'],
            classrooms: ['room_no', 'capacity'],
            syllabus: ['subject', 'topic', 'teacher_name', 'num_lectures', 'lecture_date'],
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

        const MODULE_LABELS = {
            students: 'Students', teachers: 'Teachers', classrooms: 'Classrooms', syllabus: 'Syllabus',
            attendance: 'Attendance', timetables: 'Timetable', seating: 'Exam Seating', invigilation: 'Invigilator Duty', fees: 'Fees',
        };

        let usersCache = [];

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
                                <tr><th class="p-4">Full Name</th><th class="p-4">Email</th><th class="p-4">Designation</th><th class="p-4">Module Access</th><th class="p-4">Permission</th><th class="p-4"></th></tr>
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
            usersCache = await res.json();
            const tbody = document.getElementById('usersTableBody');
            if (usersCache.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-gray-500">No staff users yet. Click '+ Add User' to grant access.</td></tr>`;
                return;
            }
            tbody.innerHTML = usersCache.map(u => `
                <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                    <td class="p-4 font-medium text-white">${esc(u.full_name)}</td>
                    <td class="p-4 text-gray-400">${esc(u.email)}</td>
                    <td class="p-4"><span class="perm-badge perm-readonly">${esc(u.designation || 'Staff')}</span></td>
                    <td class="p-4 text-xs text-gray-400">${(u.modules || []).length ? u.modules.map(m => esc(MODULE_LABELS[m] || m)).join(', ') : '<span class="text-gray-600">None</span>'}</td>
                    <td class="p-4">
                        <select onchange="changeUserPermission(${u.id}, this.value)" class="bg-[#0c0c0c] border gold-border rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none">
                            <option value="edit" ${u.permission === 'edit' ? 'selected' : ''}>Edit Access</option>
                            <option value="read_only" ${u.permission === 'read_only' ? 'selected' : ''}>Read Only</option>
                        </select>
                    </td>
                    <td class="p-4 text-right whitespace-nowrap">
                        <button onclick="openUserModal(${u.id})" title="Edit designation & module access" class="row-delete-btn fast-transition text-sm leading-none mr-3">✎</button>
                        <button onclick="removeUser(${u.id})" title="Revoke access" class="row-delete-btn fast-transition text-lg leading-none">🗑</button>
                    </td>
                </tr>
            `).join('');
        }

        function renderModuleCheckboxGrid(checkedModules) {
            const grid = document.getElementById('newUserModuleGrid');
            const checked = new Set(checkedModules || []);
            grid.innerHTML = ALL_MODULES.map(m => `
                <label class="flex items-center space-x-2 text-xs text-gray-300 bg-[#0c0c0c] border gold-border rounded-lg px-3 py-2 cursor-pointer">
                    <input type="checkbox" class="module-check newUserModuleCheckbox" value="${m}" ${checked.has(m) ? 'checked' : ''}>
                    <span>${MODULE_LABELS[m]}</span>
                </label>
            `).join('');
        }

        function openUserModal(userId) {
            const isEdit = userId !== undefined && userId !== null;
            const existing = isEdit ? usersCache.find(u => u.id === userId) : null;
            document.getElementById('userModalTitle').textContent = isEdit ? 'Edit User Access' : 'Add User';
            document.getElementById('userModalSubmitBtn').textContent = isEdit ? 'Save Changes' : 'Create User';
            document.getElementById('newUserName').value = existing ? existing.full_name : '';
            document.getElementById('newUserName').disabled = isEdit;
            document.getElementById('newUserEmail').value = existing ? existing.email : '';
            document.getElementById('newUserEmail').disabled = isEdit;
            document.getElementById('newUserPassword').value = '';
            document.getElementById('newUserPassword').placeholder = isEdit ? 'Password cannot be changed here' : 'Password (min 8 characters)';
            document.getElementById('newUserPassword').disabled = isEdit;
            document.getElementById('newUserDesignation').value = existing ? (existing.designation || '') : '';
            document.getElementById('newUserPermission').value = existing ? existing.permission : 'edit';
            renderModuleCheckboxGrid(existing ? existing.modules : []);
            document.getElementById('userModalError').textContent = '';
            document.getElementById('userModal').classList.remove('hidden');
            window.activeUserModalId = isEdit ? userId : null;
        }

        function closeUserModal() { document.getElementById('userModal').classList.add('hidden'); }

        async function submitUserForm() {
            const errorEl = document.getElementById('userModalError');
            const userId = window.activeUserModalId;
            const designation = document.getElementById('newUserDesignation').value.trim();
            const permission = document.getElementById('newUserPermission').value;
            const modules = Array.from(document.querySelectorAll('.newUserModuleCheckbox:checked')).map(el => el.value);
            if (!designation) { errorEl.textContent = 'Designation is required.'; return; }

            if (userId) {
                const res = await authFetch(`/api/users/${userId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ designation, permission, modules }),
                });
                const data = await res.json().catch(() => ({}));
                if (res.ok) { closeUserModal(); await loadUsers(); }
                else { errorEl.textContent = data.detail || 'Failed to update user.'; }
                return;
            }

            const full_name = document.getElementById('newUserName').value.trim();
            const email = document.getElementById('newUserEmail').value.trim();
            const password = document.getElementById('newUserPassword').value;
            if (!full_name || !email || !password) { errorEl.textContent = 'All fields are required.'; return; }

            const res = await authFetch('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ full_name, email, password, permission, designation, modules }),
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

        let ttTeachersData = [];
        let ttSavedConfigs = [];

        const TT_DEFAULT_TIMINGS = [
            { lecture_number: 1, time_slot: '09:00 AM - 10:00 AM' },
            { lecture_number: 2, time_slot: '10:00 AM - 11:00 AM' },
            { lecture_number: 3, time_slot: '11:15 AM - 12:15 PM' },
            { lecture_number: 4, time_slot: '01:15 PM - 02:15 PM' },
        ];

        async function renderSeatingModule(container) {
            const canWrite = myPermission !== 'read_only';
            const res = await authFetch(`/api/seating/${currentBranchId}`);
            const layouts = await res.json();
            window.seatingLayouts = layouts;
            container.innerHTML = `
                <div class="space-y-8">
                    <div class="flex justify-between items-center flex-wrap gap-3">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Exam Seating Layout</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">No same-batch students side-by-side or front-back</p>
                        </div>
                    </div>
                    ${canWrite ? `
                    <div class="glass-panel border gold-border p-6 rounded-2xl shadow-2xl">
                        <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider mb-4">Build Seating Plan</h3>
                        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                            <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Exam Date</label><input id="seatExamDate" type="date" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                            <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Room Number</label><input id="seatRoom" type="text" required placeholder="Exam Hall 204" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                            <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Rows</label><input id="seatRows" type="number" min="1" max="100" value="5" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                            <div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Columns</label><input id="seatColumns" type="number" min="1" max="100" value="8" required class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 focus:outline-none"></div>
                        </div>
                        <p class="text-xs text-gray-500 mt-4">Students are pulled from the current branch. The generator spaces batches so orthogonally adjacent seats never contain the same batch.</p>
                        <button onclick="generateSeatingPlan()" class="mt-5 gold-bg hover:opacity-95 text-black font-extrabold px-6 py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Generate Seating Plan</button>
                    </div>` : ''}
                    <div id="seatingLayoutsContainer" class="space-y-6"></div>
                </div>`;
            renderSavedSeatingLayouts();
        }

        async function generateSeatingPlan() {
            const exam_date = document.getElementById('seatExamDate').value;
            const room_number = document.getElementById('seatRoom').value.trim();
            const rows = parseInt(document.getElementById('seatRows').value, 10);
            const columns = parseInt(document.getElementById('seatColumns').value, 10);
            if (!exam_date || !room_number || !rows || !columns) { alert('Exam date, room number, rows and columns are required.'); return; }
            const res = await authFetch('/api/seating/generate', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ branch_id: currentBranchId, exam_date, room_number, rows, columns })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) { alert(data.detail || 'Could not generate seating plan.'); return; }
            alert('Exam seating plan generated successfully.');
            const listRes = await authFetch(`/api/seating/${currentBranchId}`);
            window.seatingLayouts = await listRes.json();
            renderSavedSeatingLayouts();
        }

        function renderSavedSeatingLayouts() {
            const container = document.getElementById('seatingLayoutsContainer');
            if (!container) return;
            const layouts = window.seatingLayouts || [];
            if (!layouts.length) {
                container.innerHTML = '<div class="glass-panel border gold-border p-8 rounded-2xl text-center text-gray-500">No exam seating plans yet.</div>';
                return;
            }
            const canWrite = myPermission !== 'read_only';
            container.innerHTML = layouts.map(layout => {
                const assignments = layout.assignments || [];
                const byCell = new Map(assignments.map(a => [`${a.row}-${a.column}`, a]));
                let grid = '';
                for (let r = 1; r <= layout.rows; r++) {
                    for (let c = 1; c <= layout.columns; c++) {
                        const a = byCell.get(`${r}-${c}`);
                        grid += `<div class="min-h-20 rounded-xl border ${a ? 'gold-border bg-[#10100d]' : 'border-gray-900 bg-[#080808]'} p-2 flex flex-col justify-between">${a ? `<div class="text-[11px] font-bold text-gray-100">${esc(a.name)}</div><div class="text-[10px] text-yellow-500">${esc(a.batch)}</div><div class="text-[9px] text-gray-500">Roll ${esc(a.roll_number)}</div>` : '<span class="text-[10px] text-gray-700">EMPTY</span>'}</div>`;
                    }
                }
                return `<div class="glass-panel border gold-border p-6 rounded-2xl shadow-2xl">
                    <div class="flex justify-between items-center mb-5 flex-wrap gap-3">
                        <div><h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">${esc(layout.room_number)}</h3><p class="text-xs text-gray-500 mt-1">${esc(layout.exam_date)} · ${layout.rows} × ${layout.columns} · ${assignments.length} students</p></div>
                        ${canWrite ? `<button onclick="deleteSeatingLayout(${layout.id})" class="text-red-400 border border-red-900/40 bg-[#171010] hover:bg-[#241313] px-3 py-2 rounded-lg text-xs font-bold">Delete</button>` : ''}
                    </div>
                    <div class="grid gap-2" style="grid-template-columns: repeat(${layout.columns}, minmax(80px, 1fr));">${grid}</div>
                </div>`;
            }).join('');
        }

        async function deleteSeatingLayout(layoutId) {
            if (!confirm('Delete this exam seating plan?')) return;
            const res = await authFetch(`/api/seating/${layoutId}`, { method: 'DELETE' });
            if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || 'Failed to delete seating plan.'); return; }
            window.seatingLayouts = (window.seatingLayouts || []).filter(x => x.id !== layoutId);
            renderSavedSeatingLayouts();
        }

        async function renderTimetableModule(container) {
            const canWrite = myPermission !== 'read_only';
            const [tRes, sRes, cRes] = await Promise.all([
                authFetch(`/api/records/teachers/${currentBranchId}`),
                authFetch(`/api/timetable/slots/${currentBranchId}`),
                authFetch(`/api/timetable/configs/${currentBranchId}`),
            ]);
            ttTeachersData = await tRes.json();
            window.ttSavedSlots = await sRes.json();
            ttSavedConfigs = await cRes.json();

            const batchNamesInSlots = [...new Set(window.ttSavedSlots.map(s => s.batch_name))].sort((a, b) => (a || '').localeCompare(b || ''));

            container.innerHTML = `
                <div class="space-y-8">
                    <div class="flex justify-between items-center flex-wrap gap-3">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Timetable Generation & Batch Scheduler</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Conflict-checked · one batch at a time · never stacked</p>
                        </div>
                        <div class="flex items-center gap-3">
                            <button onclick="deleteAllTimetables()" class="bg-[#171010] hover:bg-[#241313] text-red-400 border border-red-900/40 px-5 py-2.5 rounded-xl text-xs font-extrabold uppercase tracking-wider fast-transition">Delete All Timetables</button>
                            <button onclick="window.print()" class="bg-[#141414] hover:bg-[#202020] gold-gradient-text border gold-border px-5 py-2.5 rounded-xl text-xs font-extrabold uppercase tracking-wider fast-transition shadow-lg">Download PDF / Print Timetable</button>
                        </div>
                    </div>
                    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                        <div class="glass-panel border gold-border p-6 rounded-2xl space-y-6">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Configure Batch & Teacher Load</h3>
                            <div class="space-y-4">
                                ${ttSavedConfigs.length > 0 ? `
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Load Existing Batch <span class="text-gray-600 normal-case font-normal">(to edit & regenerate exactly)</span></label>
                                    <select id="ttLoadBatchSelect" onchange="loadTimetableConfig(this.value)" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-xs text-gray-200 focus:outline-none">
                                        <option value="">— New Batch —</option>
                                        ${ttSavedConfigs.map(c => `<option value="${esc(c.batch_name)}">${esc(c.batch_name)}</option>`).join('')}
                                    </select>
                                </div>` : ''}
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Batch Name</label>
                                    <input type="text" id="ttBatchName" placeholder="e.g. B.Tech CSE Batch A" value="B.Tech CSE Batch A" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                                </div>
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Lecture Timings</label>
                                    <div id="ttTimingRows" class="space-y-2"></div>
                                    <button type="button" onclick="addTimingRow()" class="mt-2 text-xs font-bold gold-gradient-text hover:opacity-80">+ Add Lecture Timing</button>
                                </div>
                                <div class="pt-2">
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Assigned Teachers & Constraints</label>
                                    <div id="teacherConfigList" class="space-y-3 max-h-60 overflow-y-auto pr-2">
                                        ${ttTeachersData.length === 0 ? '<p class="text-xs text-gray-500">No teachers found. Please add teachers first.</p>' :
                                          ttTeachersData.map((t, idx) => `
                                            <div class="p-3 bg-[#0f0f0f] border gold-border rounded-xl space-y-2" data-teacher="${esc(t.name)}" data-subject="${esc(t.subject)}">
                                                <div class="flex justify-between items-center text-xs font-bold text-gray-200"><span>${esc(t.name)} (${esc(t.subject)})</span></div>
                                                <div class="grid grid-cols-2 gap-2">
                                                    <div><label class="text-[10px] text-gray-400 uppercase">Lectures/Week</label><input type="number" id="lec_${idx}" value="3" min="1" max="5" class="w-full bg-[#070707] border gold-border rounded p-1.5 text-xs text-white"></div>
                                                    <div><label class="text-[10px] text-gray-400 uppercase">Unavailable Days</label><input type="text" id="unav_${idx}" placeholder="e.g. Monday" class="w-full bg-[#070707] border gold-border rounded p-1.5 text-xs text-white" title="Comma separated days"></div>
                                                </div>
                                            </div>
                                          `).join('')}
                                    </div>
                                </div>
                                ${canWrite
                                    ? `<button onclick="generateTimetableSchedule()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Generate / Regenerate Weekly Timetable</button>`
                                    : `<p class="text-xs text-gray-500">You have read-only access and cannot generate timetables.</p>`}
                            </div>
                        </div>
                        <div class="lg:col-span-2 glass-panel border gold-border p-6 rounded-2xl overflow-x-auto">
                            <div class="flex justify-between items-center mb-4 flex-wrap gap-3">
                                <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Generated Weekly Schedule</h3>
                                ${batchNamesInSlots.length > 0 ? `
                                <select id="ttViewBatchFilter" onchange="renderTimetableGrid()" class="bg-[#0c0c0c] border gold-border rounded-lg p-2 text-xs text-gray-200 focus:outline-none">
                                    <option value="">All Batches</option>
                                    ${batchNamesInSlots.map(b => `<option value="${esc(b)}">${esc(b)}</option>`).join('')}
                                </select>` : ''}
                            </div>
                            <table class="w-full text-left text-sm text-gray-300">
                                <thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border">
                                    <tr><th class="p-3">Batch</th><th class="p-3">Day</th><th class="p-3">Lecture #</th><th class="p-3">Time Slot</th><th class="p-3">Subject</th><th class="p-3">Teacher</th><th class="p-3">Room</th>${canWrite ? '<th class="p-3"></th>' : ''}</tr>
                                </thead>
                                <tbody id="timetableSlotsBody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
            setTimingRows(TT_DEFAULT_TIMINGS);
            renderTimetableGrid();
        }

        function timingRowHtml(lectureNumber, timeSlot) {
            return `
                <div class="flex gap-2 items-center" data-timing-row>
                    <input type="number" min="1" value="${lectureNumber}" title="Lecture Number" class="w-16 bg-[#070707] border gold-border rounded p-2 text-xs text-white tt-lecture-num">
                    <input type="text" value="${esc(timeSlot)}" placeholder="09:00 AM - 10:00 AM" title="Time Slot" class="flex-1 bg-[#070707] border gold-border rounded p-2 text-xs text-white tt-time-slot">
                    <button type="button" onclick="this.closest('[data-timing-row]').remove()" class="text-gray-500 hover:text-red-400 text-sm px-1">✕</button>
                </div>`;
        }

        function setTimingRows(timings) {
            const container = document.getElementById('ttTimingRows');
            container.innerHTML = timings.map(t => timingRowHtml(t.lecture_number, t.time_slot)).join('');
        }

        function addTimingRow() {
            const container = document.getElementById('ttTimingRows');
            const nextNum = container.children.length + 1;
            container.insertAdjacentHTML('beforeend', timingRowHtml(nextNum, ''));
        }

        function loadTimetableConfig(batchName) {
            if (!batchName) {
                document.getElementById('ttBatchName').value = '';
                setTimingRows(TT_DEFAULT_TIMINGS);
                document.querySelectorAll('#teacherConfigList > div').forEach((el, idx) => {
                    document.getElementById(`lec_${idx}`).value = 3;
                    document.getElementById(`unav_${idx}`).value = '';
                });
                return;
            }
            const config = ttSavedConfigs.find(c => c.batch_name === batchName);
            if (!config) return;
            document.getElementById('ttBatchName').value = config.batch_name;
            setTimingRows(config.timings.slice().sort((a, b) => a.lecture_number - b.lecture_number));
            document.querySelectorAll('#teacherConfigList > div').forEach((el, idx) => {
                const name = el.getAttribute('data-teacher');
                const match = config.teachers_config.find(t => t.name === name);
                document.getElementById(`lec_${idx}`).value = match ? match.lectures_per_week : 3;
                document.getElementById(`unav_${idx}`).value = (match && match.unavailable_days) ? match.unavailable_days.join(', ') : '';
            });
        }

        function renderTimetableGrid() {
            const tbody = document.getElementById('timetableSlotsBody');
            const canWrite = myPermission !== 'read_only';
            const filterEl = document.getElementById('ttViewBatchFilter');
            const filterBatch = filterEl ? filterEl.value : '';
            let slots = window.ttSavedSlots || [];
            if (filterBatch) slots = slots.filter(s => s.batch_name === filterBatch);

            const dayOrder = { Monday: 0, Tuesday: 1, Wednesday: 2, Thursday: 3, Friday: 4, Saturday: 5, Sunday: 6 };
            slots = slots.slice().sort((a, b) => {
                if (a.batch_name !== b.batch_name) return (a.batch_name || '').localeCompare(b.batch_name || '');
                const da = dayOrder[a.day] ?? 99, db = dayOrder[b.day] ?? 99;
                if (da !== db) return da - db;
                return (a.lecture_number || 0) - (b.lecture_number || 0);
            });

            if (!slots.length) {
                tbody.innerHTML = `<tr><td colspan="${canWrite ? 8 : 7}" class="p-6 text-center text-gray-500">No timetable generated yet. Configure and click generate.</td></tr>`;
                return;
            }
            tbody.innerHTML = slots.map(s => `
                <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                    <td class="p-3 text-xs text-gray-400">${esc(s.batch_name)}</td>
                    <td class="p-3 font-semibold text-yellow-500">${esc(s.day)}</td>
                    <td class="p-3 text-xs text-gray-400">${s.lecture_number ?? '—'}</td>
                    <td class="p-3">${esc(s.time_slot)}</td>
                    <td class="p-3 font-medium">${esc(s.subject)}</td>
                    <td class="p-3">${esc(s.teacher)}</td>
                    <td class="p-3 text-xs text-gray-400">${esc(s.room)}</td>
                    ${canWrite ? `<td class="p-3 text-right"><button onclick="openTimetableSlotEdit(${s.id})" title="Edit slot" class="row-delete-btn fast-transition text-sm leading-none">✎</button></td>` : ''}
                </tr>
            `).join('');
        }

        async function deleteAllTimetables() {
            if (!confirm('Delete EVERY timetable and saved timetable configuration for this branch? This cannot be undone.')) return;
            const res = await authFetch(`/api/timetable/all/${currentBranchId}`, { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                alert(`Timetable workspace cleared. ${data.slots_deleted || 0} lecture(s) removed.`);
                refreshCurrentModule();
            } else {
                alert(data.detail || 'Failed to clear timetables.');
            }
        }

        async function generateTimetableSchedule() {
            const batchName = document.getElementById('ttBatchName').value.trim();
            if (!batchName) { alert('Enter a batch name.'); return; }

            const timings = [];
            document.querySelectorAll('#ttTimingRows [data-timing-row]').forEach(row => {
                const lecture_number = parseInt(row.querySelector('.tt-lecture-num').value, 10) || 0;
                const time_slot = row.querySelector('.tt-time-slot').value.trim();
                if (time_slot) timings.push({ lecture_number, time_slot });
            });
            if (!timings.length) { alert('Add at least one lecture timing.'); return; }

            const teachers_config = [];
            document.querySelectorAll('#teacherConfigList > div').forEach((el, idx) => {
                const name = el.getAttribute('data-teacher');
                const subject = el.getAttribute('data-subject');
                const lectures_per_week = document.getElementById(`lec_${idx}`).value;
                const unavailable_days = document.getElementById(`unav_${idx}`).value.split(',').map(s => s.trim()).filter(Boolean);
                teachers_config.push({ name, subject, lectures_per_week, unavailable_days });
            });

            if (!confirm(`This erases any existing timetable for "${batchName}" and rebuilds it fresh from these settings - it never stacks on top of a prior run. Continue?`)) return;

            const res = await authFetch('/api/timetable/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ branch_id: currentBranchId, batch_name: batchName, teachers_config, timings }),
            });

            if (res.ok) {
                const result = await res.json();
                let msg = `Timetable for "${batchName}" generated and saved.`;
                if (result.warnings && result.warnings.length > 0) msg += '\\n\\nHeads up:\\n' + result.warnings.join('\\n');
                alert(msg);
                refreshCurrentModule();
            } else {
                const err = await res.json().catch(() => ({}));
                alert(err.detail || 'Failed to generate timetable.');
            }
        }

        function openTimetableSlotEdit(slotId) {
            const slot = (window.ttSavedSlots || []).find(s => s.id === slotId);
            if (!slot) return;
            window.activeTimetableSlotId = slotId;
            document.getElementById('ttSlotEditDay').value = slot.day || '';
            document.getElementById('ttSlotEditTime').value = slot.time_slot || '';
            document.getElementById('ttSlotEditSubject').value = slot.subject || '';
            document.getElementById('ttSlotEditTeacher').value = slot.teacher || '';
            document.getElementById('ttSlotEditRoom').value = slot.room || '';
            document.getElementById('timetableSlotEditModal').classList.remove('hidden');
        }

        function closeTimetableSlotEdit() { document.getElementById('timetableSlotEditModal').classList.add('hidden'); }

        async function submitTimetableSlotEdit(e) {
            e.preventDefault();
            const slotId = window.activeTimetableSlotId;
            const payload = {
                day: document.getElementById('ttSlotEditDay').value,
                time_slot: document.getElementById('ttSlotEditTime').value,
                subject: document.getElementById('ttSlotEditSubject').value,
                teacher: document.getElementById('ttSlotEditTeacher').value,
                room: document.getElementById('ttSlotEditRoom').value,
            };
            const res = await authFetch(`/api/timetable/slots/${slotId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (res.ok) {
                closeTimetableSlotEdit();
                refreshCurrentModule();
            } else {
                alert('Failed to update slot.');
            }
        }

        function openRecordModal(moduleName, recordId) {
            document.getElementById('recordModal').classList.remove('hidden');
            const isEdit = recordId !== undefined && recordId !== null;
            document.getElementById('modalTitle').textContent = isEdit ? `Edit ${moduleName} Record` : `Add New ${moduleName} Record`;
            document.getElementById('recordSubmitBtn').textContent = isEdit ? 'Save Changes' : 'Save Record';
            document.getElementById('recordFormError').textContent = '';
            const fieldsContainer = document.getElementById('modalFields');

            const existing = isEdit ? (moduleRecordsCache[moduleName] || []).find(r => r.id === recordId) : null;

            let fieldsConfig = [];
            if (moduleName === 'students') {
                fieldsConfig = [
                    { id: 'name', label: 'Name', type: 'text', placeholder: 'Aarav Sharma' },
                    { id: 'batch', label: 'Batch', type: 'text', placeholder: 'Batch A' },
                    { id: 'roll_number', label: 'Roll Number', type: 'text', placeholder: '24' },
                    { id: 'parent_contact', label: "Parent's Contact Number", type: 'text', placeholder: '+91 98765 43210' }
                ];
            } else if (moduleName === 'teachers') {
                fieldsConfig = [
                    { id: 'name', label: 'Name', type: 'text', placeholder: 'Dr. Ramesh Kumar' },
                    { id: 'subject', label: 'Subject', type: 'text', placeholder: 'Artificial Intelligence' },
                    { id: 'contact_number', label: 'Contact Number', type: 'text', placeholder: '+91 98765 43210' }
                ];
            } else if (moduleName === 'classrooms') {
                fieldsConfig = [
                    { id: 'room_no', label: 'Room Number', type: 'text', placeholder: 'Lecture Hall 402' },
                    { id: 'capacity', label: 'Seating Capacity', type: 'number', placeholder: '120' }
                ];
            } else if (moduleName === 'syllabus') {
                fieldsConfig = [
                    { id: 'subject', label: 'Subject', type: 'text', placeholder: 'Data Structures & Algorithms' },
                    { id: 'topic', label: 'Topic', type: 'text', placeholder: 'Binary Search Trees' },
                    { id: 'teacher_name', label: "Teacher's Name", type: 'text', placeholder: 'Dr. Ramesh Kumar' },
                    { id: 'num_lectures', label: 'Number of Lectures', type: 'number', placeholder: '4' },
                    { id: 'lecture_date', label: 'Date', type: 'text', placeholder: '2026-09-15' }
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
                    <input type="${f.type}" id="field_${f.id}" required placeholder="${f.placeholder}" value="${existing && existing[f.id] !== undefined && existing[f.id] !== null ? esc(existing[f.id]) : ''}" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
            `).join('');

            window.activeModalModule = moduleName;
            window.activeModalRecordId = isEdit ? recordId : null;
        }

        function closeRecordModal() { document.getElementById('recordModal').classList.add('hidden'); }

        async function submitRecordForm(e) {
            e.preventDefault();
            const errorEl = document.getElementById('recordFormError');
            errorEl.textContent = '';
            const moduleName = window.activeModalModule;
            const recordId = window.activeModalRecordId;
            const inputs = document.getElementById('modalFields').querySelectorAll('input');
            const data = {};
            inputs.forEach(input => { const key = input.id.replace('field_', ''); data[key] = input.type === 'number' ? parseFloat(input.value) : input.value; });

            const formData = new FormData();
            if (!recordId) formData.append('branch_id', currentBranchId);
            formData.append('data_json', JSON.stringify(data));

            const url = recordId ? `/api/records/${moduleName}/${recordId}` : `/api/records/${moduleName}`;
            const method = recordId ? 'PATCH' : 'POST';
            const res = await authFetch(url, { method, body: formData });

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
