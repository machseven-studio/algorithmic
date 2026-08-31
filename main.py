import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

DB_PATH = os.environ.get("DATABASE_PATH", "algorithmic.db")

app = FastAPI(title="Algorithmic Platform", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    os.makedirs("uploads", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    
    # Branches
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS branches (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    
    # Users & Sessions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        institute_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        full_name TEXT NOT NULL,
        institute_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)

    # Module 2: Students
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id TEXT PRIMARY KEY,
        branch_id TEXT NOT NULL,
        roll_number TEXT NOT NULL,
        full_name TEXT NOT NULL,
        batch TEXT NOT NULL,
        father_name TEXT NOT NULL,
        father_contact TEXT NOT NULL,
        mother_name TEXT NOT NULL,
        mother_contact TEXT NOT NULL,
        document_url TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Module 3: Teachers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id TEXT PRIMARY KEY,
        branch_id TEXT NOT NULL,
        full_name TEXT NOT NULL,
        subject TEXT NOT NULL,
        contact_number TEXT NOT NULL,
        document_url TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Module 4: Classrooms
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS classrooms (
        id TEXT PRIMARY KEY,
        branch_id TEXT NOT NULL,
        room_name TEXT NOT NULL,
        capacity INTEGER NOT NULL,
        rows INTEGER NOT NULL,
        columns INTEGER NOT NULL,
        document_url TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Module 5: Syllabus Database
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS syllabus (
        id TEXT PRIMARY KEY,
        branch_id TEXT NOT NULL,
        subject TEXT NOT NULL,
        teacher_name TEXT NOT NULL,
        lecture_date TEXT NOT NULL,
        lecture_timings TEXT NOT NULL,
        topics_covered TEXT NOT NULL,
        document_url TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Module 7: Attendance Report
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id TEXT PRIMARY KEY,
        branch_id TEXT NOT NULL,
        absentee_name TEXT NOT NULL,
        absence_date TEXT NOT NULL,
        lecture_info TEXT NOT NULL,
        reason TEXT NOT NULL,
        document_url TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Module 9: Invigilators
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS invigilators (
        id TEXT PRIMARY KEY,
        branch_id TEXT NOT NULL,
        full_name TEXT NOT NULL,
        contact_number TEXT NOT NULL,
        assigned_room TEXT,
        document_url TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Module 10: Fee Department
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fee_records (
        id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        amount_due REAL NOT NULL,
        due_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        document_url TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    cursor.execute("SELECT COUNT(*) FROM branches")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO branches (id, name, created_at) VALUES (?, ?, ?)",
                       (str(uuid.uuid4()), "Main Campus", datetime.utcnow().isoformat()))
        
    conn.commit()
    conn.close()

init_db()

# SCHEMAS
class LoginRequest(BaseModel):
    full_name: str
    institute_name: str
    email: EmailStr
    password: str

class BranchCreate(BaseModel):
    name: str

class StudentCreate(BaseModel):
    branch_id: str
    roll_number: str
    full_name: str
    batch: str
    father_name: str
    father_contact: str
    mother_name: str
    mother_contact: str
    document_url: Optional[str] = None

class TeacherCreate(BaseModel):
    branch_id: str
    full_name: str
    subject: str
    contact_number: str
    document_url: Optional[str] = None

class ClassroomCreate(BaseModel):
    branch_id: str
    room_name: str
    capacity: int
    rows: int
    columns: int
    document_url: Optional[str] = None

class SyllabusCreate(BaseModel):
    branch_id: str
    subject: str
    teacher_name: str
    lecture_date: str
    lecture_timings: str
    topics_covered: str
    document_url: Optional[str] = None

class AttendanceCreate(BaseModel):
    branch_id: str
    absentee_name: str
    absence_date: str
    lecture_info: str
    reason: str
    document_url: Optional[str] = None

class InvigilatorCreate(BaseModel):
    branch_id: str
    full_name: str
    contact_number: str
    document_url: Optional[str] = None

class FeeRecordCreate(BaseModel):
    student_id: str
    branch_id: str
    amount_due: float
    due_date: str
    document_url: Optional[str] = None

def verify_session(authorization: Optional[str] = Header(None), db: sqlite3.Connection = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication token required.")
    token = authorization.split(" ")[1]
    
    cursor = db.cursor()
    cursor.execute("SELECT token, user_id, full_name, institute_name, expires_at FROM sessions WHERE token = ?", (token,))
    session = cursor.fetchone()
    
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    
    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.utcnow() > expires_at:
        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired.")
    
    return dict(session)

# ENDPOINTS
@app.post("/api/auth/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (req.email.lower(),))
    user = cursor.fetchone()
    
    if not user:
        user_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO users (id, full_name, institute_name, email, password, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, req.full_name, req.institute_name, req.email.lower(), req.password, datetime.utcnow().isoformat()))
    else:
        user_id = user["id"]
        cursor.execute("UPDATE users SET full_name = ?, institute_name = ? WHERE id = ?", (req.full_name, req.institute_name, user_id))

    token = str(uuid.uuid4())
    expires_at = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
    
    cursor.execute("INSERT INTO sessions (token, user_id, full_name, institute_name, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (token, user_id, req.full_name, req.institute_name, datetime.utcnow().isoformat(), expires_at))
    db.commit()
    return {"token": token, "full_name": req.full_name, "institute_name": req.institute_name, "expires_at": expires_at}

@app.get("/api/branches")
def get_branches(db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name FROM branches ORDER BY created_at ASC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/branches")
def create_branch(req: BranchCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    branch_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("INSERT INTO branches (id, name, created_at) VALUES (?, ?, ?)", (branch_id, req.name, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": branch_id, "name": req.name}

# Upload Document Endpoint
@app.post("/api/upload-document")
def upload_document(file: UploadFile = File(...), session: dict = Depends(verify_session)):
    file_path = os.path.join("uploads", f"{uuid.uuid4()}_{file.filename}")
    with open(file_path, "wb") as f:
        f.write(file.file.read())
    return {"file_url": f"/{file_path}"}

# Students
@app.post("/api/students")
def add_student(req: StudentCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    student_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO students (id, branch_id, roll_number, full_name, batch, father_name, father_contact, mother_name, mother_contact, document_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (student_id, req.branch_id, req.roll_number, req.full_name, req.batch, req.father_name, req.father_contact, req.mother_name, req.mother_contact, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": student_id, "status": "created"}

@app.get("/api/students")
def list_students(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM students WHERE branch_id = ? ORDER BY roll_number ASC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Teachers
@app.post("/api/teachers")
def add_teacher(req: TeacherCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    teacher_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO teachers (id, branch_id, full_name, subject, contact_number, document_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (teacher_id, req.branch_id, req.full_name, req.subject, req.contact_number, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": teacher_id, "status": "created"}

@app.get("/api/teachers")
def list_teachers(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM teachers WHERE branch_id = ? ORDER BY full_name ASC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Classrooms
@app.post("/api/classrooms")
def add_classroom(req: ClassroomCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    room_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO classrooms (id, branch_id, room_name, capacity, rows, columns, document_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (room_id, req.branch_id, req.room_name, req.capacity, req.rows, req.columns, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": room_id, "status": "created"}

@app.get("/api/classrooms")
def list_classrooms(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM classrooms WHERE branch_id = ? ORDER BY room_name ASC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Syllabus
@app.post("/api/syllabus")
def add_syllabus(req: SyllabusCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    sys_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO syllabus (id, branch_id, subject, teacher_name, lecture_date, lecture_timings, topics_covered, document_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (sys_id, req.branch_id, req.subject, req.teacher_name, req.lecture_date, req.lecture_timings, req.topics_covered, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": sys_id, "status": "created"}

@app.get("/api/syllabus")
def list_syllabus(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM syllabus WHERE branch_id = ? ORDER BY lecture_date DESC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Attendance
@app.post("/api/attendance")
def add_attendance(req: AttendanceCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    att_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO attendance (id, branch_id, absentee_name, absence_date, lecture_info, reason, document_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (att_id, req.branch_id, req.absentee_name, req.absence_date, req.lecture_info, req.reason, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": att_id, "status": "created"}

@app.get("/api/attendance")
def list_attendance(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM attendance WHERE branch_id = ? ORDER BY absence_date DESC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Invigilators
@app.post("/api/invigilators")
def add_invigilator(req: InvigilatorCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    inv_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO invigilators (id, branch_id, full_name, contact_number, document_url, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (inv_id, req.branch_id, req.full_name, req.contact_number, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": inv_id, "status": "created"}

@app.post("/api/invigilators/auto-allocate")
def auto_allocate_invigilators(branch_id: str = Query(...), session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id FROM invigilators WHERE branch_id = ?", (branch_id,))
    invs = [row["id"] for row in cursor.fetchall()]
    cursor.execute("SELECT room_name FROM classrooms WHERE branch_id = ?", (branch_id,))
    rooms = [row["room_name"] for row in cursor.fetchall()]

    if not rooms:
        raise HTTPException(status_code=400, detail="No classrooms registered for room allocation.")

    for i, inv_id in enumerate(invs):
        assigned_room = rooms[i % len(rooms)]
        cursor.execute("UPDATE invigilators SET assigned_room = ? WHERE id = ?", (assigned_room, inv_id))

    db.commit()
    return {"status": "success", "allocated_count": len(invs)}

@app.get("/api/invigilators")
def list_invigilators(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM invigilators WHERE branch_id = ? ORDER BY full_name ASC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Fees
@app.post("/api/fees")
def add_fee_record(req: FeeRecordCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    fee_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO fee_records (id, student_id, branch_id, amount_due, due_date, status, document_url, updated_at)
    VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)
    """, (fee_id, req.student_id, req.branch_id, req.amount_due, req.due_date, req.document_url, datetime.utcnow().isoformat()))
    db.commit()
    return {"id": fee_id, "status": "created"}

@app.get("/api/fees/defaulters")
def list_defaulters(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("""
    SELECT f.id, f.amount_due, f.due_date, f.status, f.document_url, s.full_name as student_name, s.roll_number, s.father_contact
    FROM fee_records f
    JOIN students s ON f.student_id = s.id
    WHERE f.branch_id = ? AND f.status = 'PENDING'
    ORDER BY f.due_date ASC
    """, (branch_id,))
    
    records = [dict(row) for row in cursor.fetchall()]
    today = datetime.utcnow().date()
    
    for r in records:
        due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
        days_left = (due - today).days
        r["days_remaining"] = days_left
        r["urgent_alert"] = 0 <= days_left <= 3
        
    return records

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
