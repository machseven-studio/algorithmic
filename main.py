# main.py
import hashlib
import json
import os
import random
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

app = FastAPI(title="ALGORITHMIC", version="5.0.0")

# ---------------------------------------------------------------------------
# PostgreSQL configuration
#
# Set the DATABASE_URL environment variable to point at your PostgreSQL
# server, e.g.
#   postgresql://user:password@host:5432/algorithmic
# Render/Heroku style "postgres://" URLs are accepted too.
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/algorithmic"
)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
        except psycopg2.OperationalError as exc:
            raise RuntimeError(
                "Could not connect to PostgreSQL. Set the DATABASE_URL environment "
                f"variable to a reachable PostgreSQL server. Underlying error: {exc}"
            ) from exc
    return _pool


@contextmanager
def db():
    """Checkout a pooled connection; commit on success, rollback on error."""
    conn = get_pool().getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        get_pool().putconn(conn)


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

SESSION_LIFETIME_DAYS = 7
PBKDF2_ITERATIONS = 200_000
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

VALID_MODULES = ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'invigilation', 'fees']

# Every access-controllable module key (sidebar + API surface).
ALL_MODULE_KEYS = ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'timetables', 'invigilation', 'fees']

# Designations the owner ("boss") can assign, with their default module access.
# The owner can still add/revoke individual modules per user afterwards.
DESIGNATION_PRESETS = {
    "admin":      ALL_MODULE_KEYS,
    "head":       ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'timetables', 'invigilation'],
    "teacher":    ['students', 'syllabus', 'attendance', 'timetables', 'invigilation'],
    "accountant": ['students', 'fees'],
    "clerk":      ['students', 'classrooms'],
    "custom":     [],
}
VALID_DESIGNATIONS = set(DESIGNATION_PRESETS.keys())

# Editable (user-supplied) columns per module. Used by add / edit / bulk import.
MODULE_FIELDS = {
    "students":     ["name", "batch", "roll_number", "parent_contact"],
    "teachers":     ["name", "subject", "contact_number"],
    "classrooms":   ["room_no", "capacity", "building"],
    "syllabus":     ["subject", "topic", "teacher_name", "num_lectures", "lecture_date"],
    "attendance":   ["student_name", "date", "status"],
    "invigilation": ["teacher_name", "exam_date", "room"],
    "fees":         ["student_name", "amount_inr", "status", "due_date"],
}

WEEK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def init_db():
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS institutes (
                id SERIAL PRIMARY KEY,
                institute_name TEXT NOT NULL,
                full_name TEXT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staff_users (
                id SERIAL PRIMARY KEY,
                institute_id INTEGER NOT NULL REFERENCES institutes(id),
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                permission TEXT NOT NULL DEFAULT 'read_only',
                designation TEXT NOT NULL DEFAULT 'custom',
                modules TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
        """)
        # Migrations for databases created before designations existed.
        cur.execute("ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS designation TEXT NOT NULL DEFAULT 'custom'")
        cur.execute("ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS modules TEXT NOT NULL DEFAULT '[]'")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                institute_id INTEGER NOT NULL REFERENCES institutes(id),
                staff_user_id INTEGER,
                expires_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id SERIAL PRIMARY KEY,
                institute_id INTEGER NOT NULL REFERENCES institutes(id),
                name TEXT NOT NULL,
                UNIQUE(institute_id, name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                name TEXT,
                email TEXT,
                batch TEXT,
                status TEXT,
                document TEXT,
                roll_number TEXT,
                parent_contact TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                name TEXT,
                subject TEXT,
                department TEXT,
                document TEXT,
                contact_number TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS classrooms (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                room_no TEXT,
                capacity INTEGER,
                building TEXT,
                document TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS syllabus (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                subject TEXT,
                semester TEXT,
                units INTEGER,
                document TEXT,
                topic TEXT,
                teacher_name TEXT,
                num_lectures INTEGER,
                lecture_date TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                student_name TEXT,
                date TEXT,
                status TEXT,
                document TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS timetables_slots (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                batch_name TEXT,
                day TEXT,
                lecture_number INTEGER,
                time_slot TEXT,
                subject TEXT,
                teacher TEXT,
                room TEXT
            )
        """)
        cur.execute("ALTER TABLE timetables_slots ADD COLUMN IF NOT EXISTS lecture_number INTEGER")
        # Saved generation prerequisites per batch, so a timetable can be
        # regenerated later with exactly the same constraints.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS timetable_configs (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                batch_name TEXT NOT NULL,
                config TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(branch_id, batch_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS invigilation (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                teacher_name TEXT,
                exam_date TEXT,
                room TEXT,
                document TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fees (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id),
                student_name TEXT,
                amount_inr REAL,
                status TEXT,
                due_date TEXT,
                document TEXT
            )
        """)


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
    with db() as conn:
        conn.cursor().execute(
            "INSERT INTO sessions (token, institute_id, staff_user_id, expires_at) VALUES (%s, %s, %s, %s)",
            (token, institute_id, staff_user_id, expires_at),
        )
    return token


class CurrentInstitute(BaseModel):
    id: int  # institute_id - used for all data scoping, whether owner or staff
    institute_name: str
    full_name: str
    email: str
    is_owner: bool
    permission: str      # 'owner' | 'edit' | 'read_only'
    designation: str     # 'owner' | 'admin' | 'head' | 'teacher' | 'accountant' | 'clerk' | 'custom'
    modules: list        # module keys this user may access (owner: all)


def get_current_institute(authorization: str = Header(None)) -> CurrentInstitute:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]

    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM sessions WHERE token = %s", (token,))
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        if datetime.fromisoformat(session["expires_at"]) < datetime.utcnow():
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            raise HTTPException(status_code=401, detail="Session expired, please log in again")

        cur.execute("SELECT * FROM institutes WHERE id = %s", (session["institute_id"],))
        institute = cur.fetchone()
        if not institute:
            raise HTTPException(status_code=401, detail="Invalid session")

        if session["staff_user_id"] is not None:
            cur.execute("SELECT * FROM staff_users WHERE id = %s", (session["staff_user_id"],))
            staff = cur.fetchone()
            if not staff:
                raise HTTPException(status_code=401, detail="Invalid session")
            try:
                modules = json.loads(staff["modules"] or "[]")
            except (TypeError, ValueError):
                modules = []
            return CurrentInstitute(
                id=institute["id"],
                institute_name=institute["institute_name"],
                full_name=staff["full_name"],
                email=staff["email"],
                is_owner=False,
                permission=staff["permission"],
                designation=staff["designation"] or "custom",
                modules=modules,
            )

    return CurrentInstitute(
        id=institute["id"],
        institute_name=institute["institute_name"],
        full_name=institute["full_name"] or "",
        email=institute["email"],
        is_owner=True,
        permission="owner",
        designation="owner",
        modules=list(ALL_MODULE_KEYS),
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


def check_module_access(institute: CurrentInstitute, module_key: str):
    """The owner can access everything; staff only what their designation /
    per-user privileges allow."""
    if institute.is_owner:
        return
    if module_key not in institute.modules:
        raise HTTPException(status_code=403, detail=f"Your designation does not include access to the {module_key} module")


def verify_branch_ownership(branch_id: int, institute_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM branches WHERE id = %s AND institute_id = %s", (branch_id, institute_id))
        if not cur.fetchone():
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


def identity_payload(token, institute_name, full_name, is_owner, permission, designation, modules):
    return {
        "token": token,
        "institute_name": institute_name,
        "full_name": full_name,
        "is_owner": is_owner,
        "permission": permission,
        "designation": designation,
        "modules": modules,
    }


@app.post("/api/auth/signup")
def signup(req: SignupRequest):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    salt = secrets.token_hex(16)
    password_hash = hash_password(req.password, salt)

    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO institutes (institute_name, full_name, email, password_hash, password_salt, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (req.institute_name, req.full_name, req.email.lower(), password_hash, salt, datetime.utcnow().isoformat()),
            )
            institute_id = cur.fetchone()[0]
            # every new institute gets one starter branch
            cur.execute(
                "INSERT INTO branches (institute_id, name) VALUES (%s, %s)",
                (institute_id, "Main Campus"),
            )
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    token = create_session(institute_id)
    return identity_payload(token, req.institute_name, req.full_name, True, "owner", "owner", list(ALL_MODULE_KEYS))


@app.post("/api/auth/login")
def login(req: LoginRequest):
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM institutes WHERE email = %s", (req.email.lower(),))
        institute = cur.fetchone()

        # Deliberately same error for "no such email" and "wrong password" so
        # attackers can't use this endpoint to find out which emails are registered.
        invalid = HTTPException(status_code=401, detail="Invalid email or password")

        if institute:
            computed_hash = hash_password(req.password, institute["password_salt"])
            if secrets.compare_digest(computed_hash, institute["password_hash"]):
                token = create_session(institute["id"])
                return identity_payload(
                    token, institute["institute_name"], institute["full_name"] or "",
                    True, "owner", "owner", list(ALL_MODULE_KEYS),
                )

        # Not an owner account (or wrong password) - check staff logins.
        cur.execute("SELECT * FROM staff_users WHERE email = %s", (req.email.lower(),))
        staff = cur.fetchone()
        if staff:
            computed_hash = hash_password(req.password, staff["password_salt"])
            if secrets.compare_digest(computed_hash, staff["password_hash"]):
                cur.execute("SELECT * FROM institutes WHERE id = %s", (staff["institute_id"],))
                parent_institute = cur.fetchone()
                token = create_session(staff["institute_id"], staff_user_id=staff["id"])
                try:
                    modules = json.loads(staff["modules"] or "[]")
                except (TypeError, ValueError):
                    modules = []
                return identity_payload(
                    token,
                    parent_institute["institute_name"] if parent_institute else "",
                    staff["full_name"], False, staff["permission"],
                    staff["designation"] or "custom", modules,
                )

    raise invalid


@app.get("/api/auth/me")
def whoami(institute: CurrentInstitute = Depends(get_current_institute)):
    return {
        "institute_name": institute.institute_name,
        "full_name": institute.full_name,
        "is_owner": institute.is_owner,
        "permission": institute.permission,
        "designation": institute.designation,
        "modules": institute.modules,
    }


@app.post("/api/auth/logout")
def logout(authorization: str = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        with db() as conn:
            conn.cursor().execute("DELETE FROM sessions WHERE token = %s", (token,))
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
    with db() as conn:
        conn.cursor().execute("UPDATE institutes SET institute_name = %s WHERE id = %s", (name, institute.id))
    return {"institute_name": name}


# ---------------------------------------------------------------------------
# Staff users ("Manage Users") - owner-only administration
# ---------------------------------------------------------------------------

class StaffUserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    permission: str          # 'edit' | 'read_only'
    designation: str         # 'admin' | 'head' | 'teacher' | 'accountant' | 'clerk' | 'custom'
    modules: list = None     # optional explicit module list; defaults to the designation preset


class StaffUserUpdate(BaseModel):
    permission: str = None
    designation: str = None
    modules: list = None


def clean_modules(modules):
    if modules is None:
        return None
    cleaned = [m for m in modules if m in ALL_MODULE_KEYS]
    return cleaned


@app.get("/api/users")
def list_staff_users(institute: CurrentInstitute = Depends(require_owner)):
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT id, full_name, email, permission, designation, modules, created_at FROM staff_users WHERE institute_id = %s ORDER BY id",
            (institute.id,),
        )
        users = [dict(row) for row in cur.fetchall()]
    for u in users:
        try:
            u["modules"] = json.loads(u["modules"] or "[]")
        except (TypeError, ValueError):
            u["modules"] = []
    return users


@app.get("/api/users/designations")
def list_designations(institute: CurrentInstitute = Depends(require_owner)):
    """Available designations + their default module presets, for the Add User form."""
    return {"designations": DESIGNATION_PRESETS, "all_modules": ALL_MODULE_KEYS}


@app.post("/api/users")
def add_staff_user(req: StaffUserCreate, institute: CurrentInstitute = Depends(require_owner)):
    if req.permission not in ("edit", "read_only"):
        raise HTTPException(status_code=400, detail="Permission must be 'edit' or 'read_only'")
    designation = (req.designation or "").strip().lower()
    if designation not in VALID_DESIGNATIONS:
        raise HTTPException(status_code=400, detail=f"Designation must be one of: {', '.join(sorted(VALID_DESIGNATIONS))}")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    modules = clean_modules(req.modules)
    if modules is None:
        modules = list(DESIGNATION_PRESETS[designation])

    salt = secrets.token_hex(16)
    password_hash = hash_password(req.password, salt)

    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO staff_users (institute_id, full_name, email, password_hash, password_salt, permission, designation, modules, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (institute.id, req.full_name, req.email.lower(), password_hash, salt,
                 req.permission, designation, json.dumps(modules), datetime.utcnow().isoformat()),
            )
            user_id = cur.fetchone()[0]
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="A user with this email already exists")
    return {
        "id": user_id, "full_name": req.full_name, "email": req.email.lower(),
        "permission": req.permission, "designation": designation, "modules": modules,
    }


def verify_staff_ownership(user_id: int, institute_id: int):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM staff_users WHERE id = %s AND institute_id = %s", (user_id, institute_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")


@app.patch("/api/users/{user_id}")
def update_staff_user(user_id: int, req: StaffUserUpdate, institute: CurrentInstitute = Depends(require_owner)):
    verify_staff_ownership(user_id, institute.id)

    updates, params = [], []
    if req.permission is not None:
        if req.permission not in ("edit", "read_only"):
            raise HTTPException(status_code=400, detail="Permission must be 'edit' or 'read_only'")
        updates.append("permission = %s")
        params.append(req.permission)
    if req.designation is not None:
        designation = req.designation.strip().lower()
        if designation not in VALID_DESIGNATIONS:
            raise HTTPException(status_code=400, detail=f"Designation must be one of: {', '.join(sorted(VALID_DESIGNATIONS))}")
        updates.append("designation = %s")
        params.append(designation)
        # Changing designation without an explicit module list re-applies the preset.
        if req.modules is None:
            updates.append("modules = %s")
            params.append(json.dumps(DESIGNATION_PRESETS[designation]))
    if req.modules is not None:
        updates.append("modules = %s")
        params.append(json.dumps(clean_modules(req.modules)))

    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    params.append(user_id)
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute(f"UPDATE staff_users SET {', '.join(updates)} WHERE id = %s", params)
        cur.execute("SELECT id, full_name, email, permission, designation, modules FROM staff_users WHERE id = %s", (user_id,))
        row = dict(cur.fetchone())
    row["modules"] = json.loads(row["modules"] or "[]")
    return row


@app.delete("/api/users/{user_id}")
def remove_staff_user(user_id: int, institute: CurrentInstitute = Depends(require_owner)):
    verify_staff_ownership(user_id, institute.id)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE staff_user_id = %s", (user_id,))
        cur.execute("DELETE FROM staff_users WHERE id = %s", (user_id,))
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

class BranchCreate(BaseModel):
    name: str


@app.get("/api/branches")
def get_branches(institute: CurrentInstitute = Depends(get_current_institute)):
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT * FROM branches WHERE institute_id = %s ORDER BY id", (institute.id,))
        return [dict(row) for row in cur.fetchall()]


@app.post("/api/branches")
def add_branch(branch: BranchCreate, institute: CurrentInstitute = Depends(require_write_access)):
    try:
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO branches (institute_id, name) VALUES (%s, %s) RETURNING id",
                (institute.id, branch.name),
            )
            branch_id = cur.fetchone()[0]
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="Branch already exists")
    return {"id": branch_id, "name": branch.name}


@app.patch("/api/branches/{branch_id}")
def rename_branch(branch_id: int, branch: BranchCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(branch_id, institute.id)
    name = branch.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Branch name cannot be empty")
    try:
        with db() as conn:
            conn.cursor().execute("UPDATE branches SET name = %s WHERE id = %s", (name, branch_id))
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="A branch with this name already exists")
    return {"id": branch_id, "name": name}


# ---------------------------------------------------------------------------
# Generic records (students / teachers / classrooms / syllabus / attendance / invigilation / fees)
# ---------------------------------------------------------------------------

def module_or_400(module: str) -> str:
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    return module


@app.get("/api/records/{module}/{branch_id}")
def get_records(module: str, branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    module_or_400(module)
    check_module_access(institute, module)
    verify_branch_ownership(branch_id, institute.id)
    with db() as conn:
        cur = dict_cursor(conn)
        # module is validated against VALID_MODULES above, so this is safe from injection
        cur.execute(f"SELECT * FROM {module} WHERE branch_id = %s ORDER BY id", (branch_id,))
        return [dict(row) for row in cur.fetchall()]


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


def insert_record(cur, module: str, branch_id: int, data: dict, doc_filename=None) -> int:
    fields = MODULE_FIELDS[module]
    cols = ["branch_id"] + fields + ["document"]
    vals = [branch_id] + [data.get(f) for f in fields] + [doc_filename]
    placeholders = ", ".join(["%s"] * len(cols))
    cur.execute(
        f"INSERT INTO {module} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
        vals,
    )
    return cur.fetchone()[0]


@app.post("/api/records/{module}")
async def add_record(
    module: str,
    branch_id: int = Form(...),
    data_json: str = Form(...),
    file: UploadFile = File(None),
    institute: CurrentInstitute = Depends(require_write_access),
):
    module_or_400(module)
    check_module_access(institute, module)
    verify_branch_ownership(branch_id, institute.id)

    data = json.loads(data_json)
    doc_filename = save_upload(file) if file else None

    with db() as conn:
        record_id = insert_record(conn.cursor(), module, branch_id, data, doc_filename)
    return {"id": record_id, "status": "success"}


def verify_record_ownership(cur, module: str, record_id: int, institute_id: int):
    cur.execute(
        f"""SELECT {module}.id FROM {module}
            JOIN branches ON branches.id = {module}.branch_id
            WHERE {module}.id = %s AND branches.institute_id = %s""",
        (record_id, institute_id),
    )
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Record not found")


@app.put("/api/records/{module}/{record_id}")
async def update_record(
    module: str,
    record_id: int,
    data_json: str = Form(...),
    file: UploadFile = File(None),
    institute: CurrentInstitute = Depends(require_write_access),
):
    """Edit any existing record. Only the fields present in data_json are
    updated; an attached file replaces the stored document."""
    module_or_400(module)
    check_module_access(institute, module)

    data = json.loads(data_json)
    doc_filename = save_upload(file) if file else None

    fields = [f for f in MODULE_FIELDS[module] if f in data]
    if not fields and doc_filename is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    sets = [f"{f} = %s" for f in fields]
    params = [data.get(f) for f in fields]
    if doc_filename is not None:
        sets.append("document = %s")
        params.append(doc_filename)
    params.append(record_id)

    with db() as conn:
        cur = dict_cursor(conn)
        verify_record_ownership(cur, module, record_id, institute.id)
        cur.execute(f"UPDATE {module} SET {', '.join(sets)} WHERE id = %s", params)
        cur.execute(f"SELECT * FROM {module} WHERE id = %s", (record_id,))
        row = dict(cur.fetchone())
    return {"status": "updated", "record": row}


@app.delete("/api/records/{module}/{record_id}")
def delete_record(module: str, record_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    module_or_400(module)
    check_module_access(institute, module)
    with db() as conn:
        cur = conn.cursor()
        verify_record_ownership(cur, module, record_id, institute.id)
        cur.execute(f"DELETE FROM {module} WHERE id = %s", (record_id,))
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
    module_or_400(module)
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

    inserted = 0
    with db() as conn:
        cur = conn.cursor()
        for row in reader:
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            if not any(row.values()):
                continue  # skip blank rows
            insert_record(cur, module, branch_id, row)
            inserted += 1

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

    with db() as conn:
        cur = conn.cursor()
        # Re-marking the same student on the same day replaces the old mark
        # instead of piling up duplicate attendance rows.
        cur.execute(
            "DELETE FROM attendance WHERE branch_id = %s AND student_name = %s AND date = %s",
            (req.branch_id, req.student_name, req.date),
        )
        cur.execute(
            "INSERT INTO attendance (branch_id, student_name, date, status) VALUES (%s, %s, %s, %s)",
            (req.branch_id, req.student_name, req.date, req.status),
        )
    return {"status": "success"}


@app.get("/api/attendance/report/{branch_id}")
def get_attendance_report(branch_id: int, student: str, institute: CurrentInstitute = Depends(get_current_institute)):
    """Full past attendance history for one student, newest first, plus
    summary statistics - powers the 'View Report' option in the Attendance module."""
    check_module_access(institute, "attendance")
    verify_branch_ownership(branch_id, institute.id)
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT date, status FROM attendance WHERE branch_id = %s AND student_name = %s ORDER BY date DESC",
            (branch_id, student),
        )
        history = [dict(row) for row in cur.fetchall()]
    present = sum(1 for h in history if h["status"] == "Present")
    absent = sum(1 for h in history if h["status"] == "Absent")
    total = len(history)
    return {
        "student_name": student,
        "history": history,
        "present": present,
        "absent": absent,
        "total": total,
        "percentage": round(present * 100.0 / total, 1) if total else None,
    }


@app.get("/api/attendance/{branch_id}/{date}")
def get_attendance_for_date(branch_id: int, date: str, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, "attendance")
    verify_branch_ownership(branch_id, institute.id)
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT student_name, status FROM attendance WHERE branch_id = %s AND date = %s", (branch_id, date))
        return {row["student_name"]: row["status"] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Timetable generation
# ---------------------------------------------------------------------------

@app.get("/api/timetable/slots/{branch_id}")
def get_timetable_slots(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, "timetables")
    verify_branch_ownership(branch_id, institute.id)
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT * FROM timetables_slots WHERE branch_id = %s ORDER BY batch_name, lecture_number, id",
            (branch_id,),
        )
        return [dict(row) for row in cur.fetchall()]


@app.get("/api/timetable/batches/{branch_id}")
def get_timetable_batches(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    """Every batch that has a saved timetable and/or saved generation config."""
    check_module_access(institute, "timetables")
    verify_branch_ownership(branch_id, institute.id)
    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute("SELECT batch_name, config, updated_at FROM timetable_configs WHERE branch_id = %s ORDER BY batch_name", (branch_id,))
        configs = {row["batch_name"]: row for row in cur.fetchall()}
        cur.execute("SELECT batch_name, COUNT(*) AS n FROM timetables_slots WHERE branch_id = %s GROUP BY batch_name", (branch_id,))
        counts = {row["batch_name"]: row["n"] for row in cur.fetchall()}

    batches = []
    for name in sorted(set(configs) | set(counts)):
        cfg_row = configs.get(name)
        batches.append({
            "batch_name": name,
            "slot_count": counts.get(name, 0),
            "config": json.loads(cfg_row["config"]) if cfg_row else None,
            "updated_at": cfg_row["updated_at"] if cfg_row else None,
        })
    return batches


class TimetableGenerateRequest(BaseModel):
    branch_id: int
    batch_name: str
    teachers_config: list  # [{name, subject, lectures_per_week, unavailable_days: []}]
    timings: list          # [{lecture_number: 1, time: "09:00 AM - 10:00 AM"}, ...]


class TimetableRegenerateRequest(BaseModel):
    branch_id: int
    batch_name: str


class TimetableSlotUpdate(BaseModel):
    day: str = None
    lecture_number: int = None
    time_slot: str = None
    subject: str = None
    teacher: str = None
    room: str = None


def normalise_timings(timings) -> list:
    """Accepts either the new [{lecture_number, time}] shape or a legacy list of
    strings, and returns a clean, numbered list."""
    out = []
    for i, t in enumerate(timings):
        if isinstance(t, dict):
            time_str = (t.get("time") or t.get("time_slot") or "").strip()
            number = t.get("lecture_number") or (i + 1)
        else:
            time_str = str(t).strip()
            number = i + 1
        if time_str:
            out.append({"lecture_number": int(number), "time": time_str})
    return out


def run_timetable_generation(cur, branch_id: int, batch_name: str, teachers_config: list, timings: list):
    """The actual solver. IMPORTANT: the caller must already have deleted this
    batch's previous slots - generations never stack on top of each other.

    Honors every prerequisite:
      - a teacher is never scheduled on one of their unavailable days
      - a teacher never exceeds lectures_per_week
      - no two lectures for the same batch in the same day+lecture slot
      - a teacher can't be in two batches at the same day+time
      - a classroom can't host two batches at the same day+time
    Lectures are spread across the week (round-robin over available days)
    instead of being crammed into the earliest slots.
    """
    cur.execute("SELECT room_no FROM classrooms WHERE branch_id = %s ORDER BY id", (branch_id,))
    available_rooms = [row[0] for row in cur.fetchall() if row[0]]

    generated_slots = []
    warnings = []

    # batch occupancy map for the batch being generated: {(day, lecture_number)}
    batch_busy = set()

    def teacher_busy(day, time_slot, teacher_name):
        cur.execute(
            """SELECT COUNT(*) FROM timetables_slots
               WHERE branch_id = %s AND day = %s AND time_slot = %s AND teacher = %s""",
            (branch_id, day, time_slot, teacher_name),
        )
        return cur.fetchone()[0] > 0

    def find_free_room(day, time_slot):
        for candidate in available_rooms:
            cur.execute(
                """SELECT COUNT(*) FROM timetables_slots
                   WHERE branch_id = %s AND day = %s AND time_slot = %s AND room = %s""",
                (branch_id, day, time_slot, candidate),
            )
            if cur.fetchone()[0] == 0:
                return candidate
        return None

    # Shuffle teacher order so each (re)generation can produce a fresh layout
    # while still honoring every constraint.
    configs = list(teachers_config)
    random.shuffle(configs)

    for t_config in configs:
        teacher_name = t_config["name"]
        subject = t_config["subject"]
        target_lectures = int(t_config["lectures_per_week"])
        unavailable = t_config.get("unavailable_days", []) or []

        allowed_days = [d for d in WEEK_DAYS if d not in unavailable]
        if not allowed_days:
            warnings.append(f"{teacher_name}: unavailable on every weekday, no lectures scheduled.")
            continue

        assigned_count = 0
        start = random.randrange(len(allowed_days))
        for i in range(target_lectures):
            placed = False
            # Round-robin across available days so lectures spread over the week.
            day_order = [allowed_days[(start + i + k) % len(allowed_days)] for k in range(len(allowed_days))]
            for day in day_order:
                for timing in timings:
                    lecture_number = timing["lecture_number"]
                    time_slot = timing["time"]
                    if (day, lecture_number) in batch_busy:
                        continue
                    if teacher_busy(day, time_slot, teacher_name):
                        continue
                    room = find_free_room(day, time_slot)
                    if room is None:
                        room = "Unassigned (no free classroom)" if available_rooms else "Unassigned (add a classroom)"
                    cur.execute(
                        """INSERT INTO timetables_slots (branch_id, batch_name, day, lecture_number, time_slot, subject, teacher, room)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (branch_id, batch_name, day, lecture_number, time_slot, subject, teacher_name, room),
                    )
                    batch_busy.add((day, lecture_number))
                    generated_slots.append({
                        "day": day, "lecture_number": lecture_number, "time_slot": time_slot,
                        "subject": subject, "teacher": teacher_name, "room": room,
                    })
                    assigned_count += 1
                    placed = True
                    break
                if placed:
                    break
            if not placed:
                break

        if assigned_count < target_lectures:
            warnings.append(
                f"{teacher_name}: only scheduled {assigned_count}/{target_lectures} lectures "
                f"(not enough free day/time slots without a conflict)."
            )

    return generated_slots, warnings


@app.post("/api/timetable/generate")
def generate_timetable(req: TimetableGenerateRequest, institute: CurrentInstitute = Depends(require_write_access)):
    check_module_access(institute, "timetables")
    verify_branch_ownership(req.branch_id, institute.id)

    batch_name = req.batch_name.strip()
    if not batch_name:
        raise HTTPException(status_code=400, detail="Batch name cannot be empty")
    timings = normalise_timings(req.timings)
    if not timings:
        raise HTTPException(status_code=400, detail="Add at least one lecture timing")
    if not req.teachers_config:
        raise HTTPException(status_code=400, detail="Select at least one teacher")

    config = {"teachers_config": req.teachers_config, "timings": timings}

    with db() as conn:
        cur = conn.cursor()
        # Erase this batch's previous timetable FIRST - never stack two
        # generations on top of one another. Other batches stay intact.
        cur.execute("DELETE FROM timetables_slots WHERE branch_id = %s AND batch_name = %s", (req.branch_id, batch_name))
        # Persist the prerequisites so this exact timetable can be regenerated later.
        cur.execute(
            """INSERT INTO timetable_configs (branch_id, batch_name, config, updated_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (branch_id, batch_name) DO UPDATE SET config = EXCLUDED.config, updated_at = EXCLUDED.updated_at""",
            (req.branch_id, batch_name, json.dumps(config), datetime.utcnow().isoformat()),
        )
        slots, warnings = run_timetable_generation(cur, req.branch_id, batch_name, req.teachers_config, timings)

    return {"status": "success", "batch_name": batch_name, "slots": slots, "warnings": warnings}


@app.post("/api/timetable/regenerate")
def regenerate_timetable(req: TimetableRegenerateRequest, institute: CurrentInstitute = Depends(require_write_access)):
    """Regenerate one specific batch's timetable from its saved prerequisites
    (unavailable days, lectures per week, timings...). The old timetable is
    erased first, then the new one is generated - nothing stacks."""
    check_module_access(institute, "timetables")
    verify_branch_ownership(req.branch_id, institute.id)

    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            "SELECT config FROM timetable_configs WHERE branch_id = %s AND batch_name = %s",
            (req.branch_id, req.batch_name),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No saved configuration for this batch - generate it once first")
        config = json.loads(row["config"])

        plain = conn.cursor()
        plain.execute("DELETE FROM timetables_slots WHERE branch_id = %s AND batch_name = %s", (req.branch_id, req.batch_name))
        slots, warnings = run_timetable_generation(
            plain, req.branch_id, req.batch_name,
            config["teachers_config"], normalise_timings(config["timings"]),
        )

    return {"status": "success", "batch_name": req.batch_name, "slots": slots, "warnings": warnings}


@app.patch("/api/timetable/slots/{slot_id}")
def update_timetable_slot(slot_id: int, req: TimetableSlotUpdate, institute: CurrentInstitute = Depends(require_write_access)):
    """Manually edit a single timetable cell (day, lecture number/time, subject,
    teacher or room). Conflicting edits are rejected with a clear reason."""
    check_module_access(institute, "timetables")

    with db() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            """SELECT ts.* FROM timetables_slots ts
               JOIN branches b ON b.id = ts.branch_id
               WHERE ts.id = %s AND b.institute_id = %s""",
            (slot_id, institute.id),
        )
        slot = cur.fetchone()
        if not slot:
            raise HTTPException(status_code=404, detail="Timetable slot not found")

        new_day = req.day if req.day is not None else slot["day"]
        new_lecture_number = req.lecture_number if req.lecture_number is not None else slot["lecture_number"]
        new_time = req.time_slot if req.time_slot is not None else slot["time_slot"]
        new_subject = req.subject if req.subject is not None else slot["subject"]
        new_teacher = req.teacher if req.teacher is not None else slot["teacher"]
        new_room = req.room if req.room is not None else slot["room"]

        if new_day not in WEEK_DAYS:
            raise HTTPException(status_code=400, detail=f"Day must be one of: {', '.join(WEEK_DAYS)}")

        # Conflict checks (excluding this slot itself).
        cur.execute(
            """SELECT COUNT(*) AS n FROM timetables_slots
               WHERE branch_id = %s AND batch_name = %s AND day = %s AND lecture_number = %s AND id != %s""",
            (slot["branch_id"], slot["batch_name"], new_day, new_lecture_number, slot_id),
        )
        if cur.fetchone()["n"] > 0:
            raise HTTPException(status_code=400, detail=f"{slot['batch_name']} already has a lecture at {new_day}, lecture {new_lecture_number}")
        cur.execute(
            """SELECT batch_name FROM timetables_slots
               WHERE branch_id = %s AND day = %s AND time_slot = %s AND teacher = %s AND id != %s""",
            (slot["branch_id"], new_day, new_time, new_teacher, slot_id),
        )
        clash = cur.fetchone()
        if clash:
            raise HTTPException(status_code=400, detail=f"{new_teacher} already teaches {clash['batch_name']} at {new_day} {new_time}")
        if new_room and not new_room.startswith("Unassigned"):
            cur.execute(
                """SELECT batch_name FROM timetables_slots
                   WHERE branch_id = %s AND day = %s AND time_slot = %s AND room = %s AND id != %s""",
                (slot["branch_id"], new_day, new_time, new_room, slot_id),
            )
            clash = cur.fetchone()
            if clash:
                raise HTTPException(status_code=400, detail=f"Room {new_room} is already used by {clash['batch_name']} at {new_day} {new_time}")

        cur.execute(
            """UPDATE timetables_slots
               SET day = %s, lecture_number = %s, time_slot = %s, subject = %s, teacher = %s, room = %s
               WHERE id = %s""",
            (new_day, new_lecture_number, new_time, new_subject, new_teacher, new_room, slot_id),
        )
        cur.execute("SELECT * FROM timetables_slots WHERE id = %s", (slot_id,))
        updated = dict(cur.fetchone())
    return {"status": "updated", "slot": updated}


@app.delete("/api/timetable/slots/{slot_id}")
def delete_timetable_slot(slot_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    check_module_access(institute, "timetables")
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT ts.id FROM timetables_slots ts
               JOIN branches b ON b.id = ts.branch_id
               WHERE ts.id = %s AND b.institute_id = %s""",
            (slot_id, institute.id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Timetable slot not found")
        cur.execute("DELETE FROM timetables_slots WHERE id = %s", (slot_id,))
    return {"status": "deleted"}


@app.delete("/api/timetable/batch/{branch_id}")
def delete_timetable_batch(branch_id: int, batch_name: str, institute: CurrentInstitute = Depends(require_write_access)):
    """Erase one batch's entire timetable (and its saved config)."""
    check_module_access(institute, "timetables")
    verify_branch_ownership(branch_id, institute.id)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM timetables_slots WHERE branch_id = %s AND batch_name = %s", (branch_id, batch_name))
        cur.execute("DELETE FROM timetable_configs WHERE branch_id = %s AND batch_name = %s", (branch_id, batch_name))
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Dashboard analytics
# ---------------------------------------------------------------------------

@app.get("/api/analytics/{branch_id}")
def get_analytics(branch_id: int, week_start: str = None, today: str = None,
                  institute: CurrentInstitute = Depends(get_current_institute)):
    """Aggregated numbers for the dashboard: headline counts, this week's
    attendance per batch, a 7-day attendance trend, and fee collection status.
    week_start/today come from the client so 'this week' matches the viewer's
    local calendar, not the server's."""
    verify_branch_ownership(branch_id, institute.id)

    try:
        today_dt = datetime.strptime(today, "%Y-%m-%d") if today else datetime.utcnow()
    except ValueError:
        today_dt = datetime.utcnow()
    try:
        week_start_dt = datetime.strptime(week_start, "%Y-%m-%d") if week_start else today_dt - timedelta(days=today_dt.weekday())
    except ValueError:
        week_start_dt = today_dt - timedelta(days=today_dt.weekday())
    week_dates = [(week_start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    trend_dates = [(today_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

    with db() as conn:
        cur = dict_cursor(conn)

        cur.execute("SELECT COUNT(*) AS n FROM students WHERE branch_id = %s", (branch_id,))
        n_students = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM teachers WHERE branch_id = %s", (branch_id,))
        n_teachers = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM classrooms WHERE branch_id = %s", (branch_id,))
        n_classrooms = cur.fetchone()["n"]

        # Students per batch
        cur.execute(
            """SELECT COALESCE(NULLIF(TRIM(batch), ''), 'Unassigned') AS batch, COUNT(*) AS n
               FROM students WHERE branch_id = %s GROUP BY 1 ORDER BY 1""",
            (branch_id,),
        )
        students_per_batch = [dict(r) for r in cur.fetchall()]

        # Attendance this week, per batch (attendance rows joined to students for batch)
        cur.execute(
            """SELECT COALESCE(NULLIF(TRIM(s.batch), ''), 'Unassigned') AS batch,
                      SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present,
                      COUNT(*) AS total
               FROM attendance a
               JOIN students s ON s.branch_id = a.branch_id AND s.name = a.student_name
               WHERE a.branch_id = %s AND a.date = ANY(%s)
               GROUP BY 1 ORDER BY 1""",
            (branch_id, week_dates),
        )
        attendance_week = [
            {"batch": r["batch"], "present": int(r["present"] or 0), "total": int(r["total"]),
             "percentage": round((r["present"] or 0) * 100.0 / r["total"], 1) if r["total"] else 0}
            for r in cur.fetchall()
        ]

        # 7-day overall attendance trend
        cur.execute(
            """SELECT date,
                      SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present,
                      COUNT(*) AS total
               FROM attendance WHERE branch_id = %s AND date = ANY(%s)
               GROUP BY date""",
            (branch_id, trend_dates),
        )
        by_date = {r["date"]: r for r in cur.fetchall()}
        attendance_trend = []
        for d in trend_dates:
            r = by_date.get(d)
            attendance_trend.append({
                "date": d,
                "present": int(r["present"]) if r else 0,
                "total": int(r["total"]) if r else 0,
                "percentage": round(r["present"] * 100.0 / r["total"], 1) if r and r["total"] else None,
            })

        # Today's attendance headline
        cur.execute(
            """SELECT SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present, COUNT(*) AS total
               FROM attendance WHERE branch_id = %s AND date = %s""",
            (branch_id, today_dt.strftime("%Y-%m-%d")),
        )
        r = cur.fetchone()
        attendance_today = {
            "present": int(r["present"] or 0), "total": int(r["total"] or 0),
            "percentage": round((r["present"] or 0) * 100.0 / r["total"], 1) if r["total"] else None,
        }

        # Fees: paid vs pending
        cur.execute(
            """SELECT
                 SUM(CASE WHEN LOWER(COALESCE(status,'')) = 'paid' THEN COALESCE(amount_inr,0) ELSE 0 END) AS paid_total,
                 SUM(CASE WHEN LOWER(COALESCE(status,'')) != 'paid' THEN COALESCE(amount_inr,0) ELSE 0 END) AS pending_total,
                 SUM(CASE WHEN LOWER(COALESCE(status,'')) = 'paid' THEN 1 ELSE 0 END) AS paid_count,
                 SUM(CASE WHEN LOWER(COALESCE(status,'')) != 'paid' THEN 1 ELSE 0 END) AS pending_count
               FROM fees WHERE branch_id = %s""",
            (branch_id,),
        )
        f = cur.fetchone()
        fees = {
            "paid_total": float(f["paid_total"] or 0),
            "pending_total": float(f["pending_total"] or 0),
            "paid_count": int(f["paid_count"] or 0),
            "pending_count": int(f["pending_count"] or 0),
        }

        # Students with pending fees (top 5 by amount, for the dashboard list)
        cur.execute(
            """SELECT student_name, amount_inr, due_date FROM fees
               WHERE branch_id = %s AND LOWER(COALESCE(status,'')) != 'paid'
               ORDER BY COALESCE(amount_inr,0) DESC LIMIT 5""",
            (branch_id,),
        )
        fees_pending_list = [dict(r) for r in cur.fetchall()]

        # Today's full lecture schedule per batch; the client works out which
        # ones are ongoing at the exact minute it is being viewed.
        weekday_name = today_dt.strftime("%A")
        cur.execute(
            """SELECT batch_name, day, lecture_number, time_slot, subject, teacher, room
               FROM timetables_slots WHERE branch_id = %s AND day = %s
               ORDER BY batch_name, lecture_number""",
            (branch_id, weekday_name),
        )
        todays_lectures = [dict(r) for r in cur.fetchall()]

    return {
        "counts": {"students": n_students, "teachers": n_teachers, "classrooms": n_classrooms},
        "students_per_batch": students_per_batch,
        "attendance_week_per_batch": attendance_week,
        "attendance_trend": attendance_trend,
        "attendance_today": attendance_today,
        "fees": fees,
        "fees_pending_list": fees_pending_list,
        "todays_lectures": todays_lectures,
        "weekday": weekday_name,
        "week_dates": week_dates,
    }


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

FRONTEND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.get("/", response_class=HTMLResponse)
def read_root():
    with open(FRONTEND_FILE, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)
