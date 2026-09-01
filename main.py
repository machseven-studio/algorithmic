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

app = FastAPI(title="ALGORITHMIC", version="5.0.0")

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
            batch TEXT,
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
            contact_number TEXT,
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
            topic TEXT,
            teacher_name TEXT,
            num_lectures INTEGER,
            date TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            student_name TEXT,
            batch TEXT,
            date TEXT,
            status TEXT,
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
    id: int
    institute_name: str
    full_name: str
    email: str
    is_owner: bool
    permission: str


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
    if institute.permission == "read_only":
        raise HTTPException(status_code=403, detail="Your account has read-only access")
    return institute


def require_owner(institute: CurrentInstitute = Depends(get_current_institute)) -> CurrentInstitute:
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
# Staff users ("Manage Users")
# ---------------------------------------------------------------------------

class StaffUserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    permission: str


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
# Records API
# ---------------------------------------------------------------------------

@app.get("/api/records/{module}/{branch_id}")
def get_records(module: str, branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail="Invalid module")
    verify_branch_ownership(branch_id, institute.id)

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if module == 'students':
        cursor.execute("SELECT id, name, batch, roll_number, parent_contact FROM students WHERE branch_id = ? ORDER BY batch ASC", (branch_id,))
    elif module == 'teachers':
        cursor.execute("SELECT id, name, subject, contact_number FROM teachers WHERE branch_id = ?", (branch_id,))
    elif module == 'syllabus':
        cursor.execute("SELECT id, subject, topic, teacher_name, num_lectures, date FROM syllabus WHERE branch_id = ?", (branch_id,))
    else:
        cursor.execute(f"SELECT * FROM {module} WHERE branch_id = ?", (branch_id,))
        
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return records


def save_upload(file: UploadFile) -> str:
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
        cursor.execute("INSERT INTO students (branch_id, name, batch, roll_number, parent_contact) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('name'), data.get('batch'), data.get('roll_number'), data.get('parent_contact')))
    elif module == 'teachers':
        cursor.execute("INSERT INTO teachers (branch_id, name, subject, contact_number) VALUES (?, ?, ?, ?)",
                       (branch_id, data.get('name'), data.get('subject'), data.get('contact_number')))
    elif module == 'classrooms':
        cursor.execute("INSERT INTO classrooms (branch_id, room_no, capacity, building, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('room_no'), data.get('capacity'), data.get('building'), doc_filename))
    elif module == 'syllabus':
        cursor.execute("INSERT INTO syllabus (branch_id, subject, topic, teacher_name, num_lectures, date) VALUES (?, ?, ?, ?, ?, ?)",
                       (branch_id, data.get('subject'), data.get('topic'), data.get('teacher_name'), data.get('num_lectures'), data.get('date')))
    elif module == 'attendance':
        cursor.execute("INSERT INTO attendance (branch_id, student_name, batch, date, status) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('student_name'), data.get('batch'), data.get('date', datetime.utcnow().strftime('%Y-%m-%d')), data.get('status')))
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


BULK_IMPORT_COLUMNS = {
    "students": ["name", "batch", "roll_number", "parent_contact"],
    "teachers": ["name", "subject", "contact_number"],
    "classrooms": ["room_no", "capacity"],
    "syllabus": ["subject", "topic", "teacher_name", "num_lectures", "date"],
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
            continue

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
            cursor.execute("INSERT INTO syllabus (branch_id, subject, topic, teacher_name, num_lectures, date) VALUES (?, ?, ?, ?, ?, ?)",
                           (branch_id, row.get('subject'), row.get('topic'), row.get('teacher_name'), row.get('num_lectures'), row.get('date')))
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
# Attendance API endpoints
# ---------------------------------------------------------------------------

class AttendanceRecordRequest(BaseModel):
    branch_id: int
    student_name: str
    batch: str
    date: str
    status: str

@app.post("/api/attendance/mark")
def mark_attendance(req: AttendanceRecordRequest, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO attendance (branch_id, student_name, batch, date, status)
        VALUES (?, ?, ?, ?, ?)
    """, (req.branch_id, req.student_name, req.batch, req.date, req.status))
    conn.commit()
    conn.close()
    return {"status": "success"}


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
    teachers_config: list
    timings: list


@app.post("/api/timetable/generate")
def generate_timetable(req: TimetableGenerateRequest, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(req.branch_id, institute.id)

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM timetables_slots WHERE branch_id = ? AND batch_name = ?", (req.branch_id, req.batch_name))
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

                cursor.execute("""
                    SELECT COUNT(*) FROM timetables_slots
                    WHERE branch_id = ? AND batch_name = ? AND day = ? AND time_slot = ?
                """, (req.branch_id, req.batch_name, day, slot_time))
                if cursor.fetchone()[0] > 0:
                    continue

                cursor.execute("""
                    SELECT COUNT(*) FROM timetables_slots
                    WHERE branch_id = ? AND day = ? AND time_slot = ? AND teacher = ?
                """, (req.branch_id, day, slot_time, teacher_name))
                if cursor.fetchone()[0] > 0:
                    continue

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
                f"{teacher_name}: only scheduled {assigned_count}/{target_lectures} lectures."
            )

    conn.commit()
    conn.close()
    return {"status": "success", "slots": generated_slots, "warnings": warnings}


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
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700;800;900&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #030303;
            color: #f3f4f6;
            overflow-x: hidden;
        }

        .welcome-font {
            font-family: 'Cinzel', serif;
            font-weight: 800;
            letter-spacing: 0.05em;
        }

        .gold-textured-text {
            background: linear-gradient(135deg, #bf953f 0%, #fcf6ba 25%, #b38728 50%, #fbf5b7 75%, #aa771c 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            filter: drop-shadow(0 2px 4px rgba(0,0,0,0.6));
        }

        .welcome-animation {
            animation: welcomeFade 1.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }

        @keyframes welcomeFade {
            0% { opacity: 0; transform: translateY(-15px) scale(0.98); }
            100% { opacity: 1; transform: translateY(0) scale(1); }
        }

        .glass-panel {
            background: rgba(10, 10, 10, 0.92);
            border: 1px solid rgba(212, 175, 55, 0.18);
            box-shadow: 0 12px 32px rgba(0,0,0,0.55);
        }

        .gold-border { border-color: rgba(212, 175, 55, 0.28); }
        .gold-bg { background: linear-gradient(135deg, #EACD6E, #AA771C); color: #000; }
        .sidebar-item:hover, .sidebar-item.active {
            background: rgba(212, 175, 55, 0.12);
            color: #E8C767;
            border-left: 3px solid #D4AF37;
        }
    </style>
</head>
<body class="min-h-screen flex flex-col">

    <div id="appContainer" class="min-h-screen flex flex-col">
        <header class="border-b gold-border bg-[#0a0a0a] px-8 py-4 flex justify-between items-center sticky top-0 z-40">
            <h1 id="headerInstituteName" class="welcome-font text-2xl gold-textured-text">—</h1>
            <div id="headerFullName" class="font-bold text-gray-400 text-sm"></div>
        </header>

        <div class="flex flex-1">
            <nav class="w-64 border-r gold-border bg-[#0b0b0b] flex flex-col py-6 space-y-1.5 shrink-0">
                <button onclick="switchModule('home')" class="sidebar-item active w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300">Home Dashboard</button>
                <button onclick="switchModule('students')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300">Students</button>
                <button onclick="switchModule('teachers')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300">Teachers</button>
                <button onclick="switchModule('syllabus')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300">Syllabus</button>
                <button onclick="switchModule('attendance')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300">Attendance</button>
            </nav>

            <main class="flex-1 p-10 overflow-y-auto bg-[#070707]" id="mainContent"></main>
        </div>
    </div>

    <script>
        let currentUser = { full_name: "Samarth Dave" };
        let currentModule = 'home';
        let currentBranchId = 1;

        function switchModule(mod) {
            currentModule = mod;
            refreshCurrentModule();
        }

        async function refreshCurrentModule() {
            const container = document.getElementById('mainContent');
            if (currentModule === 'home') {
                container.innerHTML = `
                    <div class="welcome-animation space-y-6">
                        <div class="glass-panel p-12 rounded-3xl border gold-border text-center">
                            <h2 class="welcome-font text-5xl gold-textured-text mb-4">Welcome, ${currentUser.full_name}</h2>
                        </div>
                    </div>
                `;
            } else if (currentModule === 'students') {
                renderStudentModule(container);
            } else if (currentModule === 'teachers') {
                renderTeacherModule(container);
            } else if (currentModule === 'syllabus') {
                renderSyllabusModule(container);
            } else if (currentModule === 'attendance') {
                renderAttendanceModule(container);
            }
        }

        async function renderStudentModule(container) {
            const res = await fetch(\`/api/records/students/\${currentBranchId}\`);
            const students = await res.json();
            
            container.innerHTML = \`
                <div class="space-y-6">
                    <div class="flex justify-between items-center">
                        <h2 class="text-2xl font-black gold-textured-text uppercase">Student Department</h2>
                        <input type="text" id="studentSearch" onkeyup="filterStudents()" placeholder="Search Student Name..." class="bg-[#0c0c0c] border gold-border rounded-xl px-4 py-2 text-sm text-gray-200">
                    </div>
                    <div class="glass-panel border gold-border rounded-2xl p-6">
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead class="bg-[#121212] text-xs uppercase gold-textured-text border-b gold-border">
                                <tr>
                                    <th class="p-4">Name</th>
                                    <th class="p-4">Batch</th>
                                    <th class="p-4">Roll Number</th>
                                    <th class="p-4">Parent's Contact Number</th>
                                </tr>
                            </thead>
                            <tbody id="studentTableBody">
                                \${students.map(s => \`
                                    <tr class="border-b border-gray-900 student-row" data-name="\${s.name.toLowerCase()}">
                                        <td class="p-4 font-semibold text-white">\${s.name}</td>
                                        <td class="p-4">\${s.batch}</td>
                                        <td class="p-4">\${s.roll_number}</td>
                                        <td class="p-4">\${s.parent_contact}</td>
                                    </tr>
                                \`).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            \`;
        }

        function filterStudents() {
            const query = document.getElementById('studentSearch').value.toLowerCase();
            const rows = document.querySelectorAll('.student-row');
            rows.forEach(r => {
                const name = r.getAttribute('data-name');
                r.style.display = name.includes(query) ? '' : 'none';
            });
        }

        async function renderTeacherModule(container) {
            const res = await fetch(\`/api/records/teachers/\${currentBranchId}\`);
            const teachers = await res.json();
            
            container.innerHTML = \`
                <div class="space-y-6">
                    <h2 class="text-2xl font-black gold-textured-text uppercase">Teacher Department</h2>
                    <div class="glass-panel border gold-border rounded-2xl p-6">
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead class="bg-[#121212] text-xs uppercase gold-textured-text border-b gold-border">
                                <tr>
                                    <th class="p-4">Name</th>
                                    <th class="p-4">Subject</th>
                                    <th class="p-4">Contact Number</th>
                                </tr>
                            </thead>
                            <tbody>
                                \${teachers.map(t => \`
                                    <tr class="border-b border-gray-900">
                                        <td class="p-4 font-semibold text-white">\${t.name}</td>
                                        <td class="p-4">\${t.subject}</td>
                                        <td class="p-4">\${t.contact_number}</td>
                                    </tr>
                                \`).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            \`;
        }

        async function renderSyllabusModule(container) {
            const res = await fetch(\`/api/records/syllabus/\${currentBranchId}\`);
            const syllabus = await res.json();
            
            container.innerHTML = \`
                <div class="space-y-6">
                    <h2 class="text-2xl font-black gold-textured-text uppercase">Syllabus Department</h2>
                    <div class="glass-panel border gold-border rounded-2xl p-6">
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead class="bg-[#121212] text-xs uppercase gold-textured-text border-b gold-border">
                                <tr>
                                    <th class="p-4">Subject</th>
                                    <th class="p-4">Topic</th>
                                    <th class="p-4">Teacher's Name</th>
                                    <th class="p-4">Number of Lectures</th>
                                    <th class="p-4">Date</th>
                                </tr>
                            </thead>
                            <tbody>
                                \${syllabus.map(s => \`
                                    <tr class="border-b border-gray-900">
                                        <td class="p-4 font-semibold text-white">\${s.subject}</td>
                                        <td class="p-4">\${s.topic}</td>
                                        <td class="p-4">\${s.teacher_name}</td>
                                        <td class="p-4">\${s.num_lectures}</td>
                                        <td class="p-4">\${s.date}</td>
                                    </tr>
                                \`).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            \`;
        }

        async function renderAttendanceModule(container) {
            const res = await fetch(\`/api/records/students/\${currentBranchId}\`);
            const students = await res.json();
            const batches = [...new Set(students.map(s => s.batch))].sort();

            container.innerHTML = \`
                <div class="space-y-6">
                    <div class="flex justify-between items-center">
                        <h2 class="text-2xl font-black gold-textured-text uppercase">Attendance Module</h2>
                        <div class="flex space-x-4">
                            <select id="attendanceBatchSelect" onchange="loadBatchAttendance()" class="bg-[#0c0c0c] border gold-border rounded-xl px-4 py-2 text-sm text-gray-200">
                                <option value="">Select Batch</option>
                                \${batches.map(b => \`<option value="\${b}">\${b}</option>\`).join('')}
                            </select>
                            <input type="text" id="attendanceSearch" onkeyup="filterAttendance()" placeholder="Search Student..." class="bg-[#0c0c0c] border gold-border rounded-xl px-4 py-2 text-sm text-gray-200">
                        </div>
                    </div>
                    <div id="attendanceContainer" class="glass-panel border gold-border rounded-2xl p-6">
                        <p class="text-gray-500 text-center py-6">Please select a batch to record attendance.</p>
                    </div>
                </div>
            \`;
            window.allStudentsCache = students;
        }

        function loadBatchAttendance() {
            const batch = document.getElementById('attendanceBatchSelect').value;
            const container = document.getElementById('attendanceContainer');
            if (!batch) {
                container.innerHTML = '<p class="text-gray-500 text-center py-6">Please select a batch to record attendance.</p>';
                return;
            }

            const batchStudents = window.allStudentsCache.filter(s => s.batch === batch);
            container.innerHTML = \`
                <table class="w-full text-left text-sm text-gray-300">
                    <thead class="bg-[#121212] text-xs uppercase gold-textured-text border-b gold-border">
                        <tr>
                            <th class="p-4">Student Name</th>
                            <th class="p-4 text-right">Mark Attendance</th>
                        </tr>
                    </thead>
                    <tbody id="attendanceTableBody">
                        \${batchStudents.map(s => \`
                            <tr class="border-b border-gray-900 attendance-row" data-name="\${s.name.toLowerCase()}">
                                <td class="p-4 font-semibold text-white">\${s.name}</td>
                                <td class="p-4 text-right space-x-3">
                                    <button onclick="markStatus('\${s.name}', '\${s.batch}', 'Present', this)" class="bg-green-900/40 border border-green-600 hover:bg-green-600 text-green-200 px-4 py-1 rounded-lg text-xs">Present</button>
                                    <button onclick="markStatus('\${s.name}', '\${s.batch}', 'Absent', this)" class="bg-red-900/40 border border-red-600 hover:bg-red-600 text-red-200 px-4 py-1 rounded-lg text-xs">Absent</button>
                                </td>
                            </tr>
                        \`).join('')}
                    </tbody>
                </table>
            \`;
        }

        function filterAttendance() {
            const query = document.getElementById('attendanceSearch').value.toLowerCase();
            const rows = document.querySelectorAll('.attendance-row');
            rows.forEach(r => {
                const name = r.getAttribute('data-name');
                r.style.display = name.includes(query) ? '' : 'none';
            });
        }

        async function markStatus(name, batch, status, btn) {
            await fetch('/api/attendance/mark', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    branch_id: currentBranchId,
                    student_name: name,
                    batch: batch,
                    date: new Date().toISOString().split('T')[0],
                    status: status
                })
            });
            const parent = btn.parentElement;
            parent.innerHTML = \`<span class="text-xs font-bold \${status === 'Present' ? 'text-green-400' : 'text-red-400'}">\${status}</span>\`;
        }

        refreshCurrentModule();
    </script>
</body>
</html>
"""
