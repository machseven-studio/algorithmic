import os
import sqlite3
import uuid
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr

DB_PATH = os.environ.get("DATABASE_PATH", "algorithmic.db")
SECRET_KEY = os.environ.get("SESSION_SECRET", "algorithmic-super-secret-key-2026")

app = FastAPI(title="Algorithmic Platform", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------
# DATABASE CONNECTION & INITIALIZATION
# ------------------------------------------------------------------------------
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    
    # Branches Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS branches (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    
    # Users & Sessions Table (Full Name & Audit support)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TEXT NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        full_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    """)
    
    # Audit Trail Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id TEXT PRIMARY KEY,
        user_name TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT NOT NULL,
        timestamp TEXT NOT NULL
    );
    """)

    # Module 2: Student Database
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
        email TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Fee Department Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fee_records (
        id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        amount_due REAL NOT NULL,
        due_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        updated_at TEXT NOT NULL,
        FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (branch_id) REFERENCES branches (id) ON DELETE CASCADE
    );
    """)

    # Seed Default Branch if none
    cursor.execute("SELECT COUNT(*) FROM branches")
    if cursor.fetchone()[0] == 0:
        main_branch_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO branches (id, name, created_at) VALUES (?, ?, ?)",
                       (main_branch_id, "Main Campus", datetime.utcnow().isoformat()))
        
    conn.commit()
    conn.close()

init_db()

# ------------------------------------------------------------------------------
# AUTHENTICATION & SESSION AUTH DEPENDENCY
# ------------------------------------------------------------------------------
class LoginRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str

class BranchCreate(BaseModel):
    name: str

class BranchUpdate(BaseModel):
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
    email: Optional[str] = ""

class FeeRecordCreate(BaseModel):
    student_id: str
    branch_id: str
    amount_due: float
    due_date: str  # YYYY-MM-DD

def verify_session(authorization: Optional[str] = Header(None), db: sqlite3.Connection = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication token required.")
    token = authorization.split(" ")[1]
    
    cursor = db.cursor()
    cursor.execute("SELECT token, user_id, full_name, expires_at FROM sessions WHERE token = ?", (token,))
    session = cursor.fetchone()
    
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    
    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.utcnow() > expires_at:
        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    
    return {"user_id": session["user_id"], "full_name": session["full_name"], "token": token}

def log_audit(db: sqlite3.Connection, user_name: str, branch_id: str, action: str, details: str):
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO audit_logs (id, user_name, branch_id, action, details, timestamp)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (str(uuid.uuid4()), user_name, branch_id, action, details, datetime.utcnow().isoformat()))
    db.commit()

# ------------------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------------------
@app.post("/api/auth/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, password FROM users WHERE email = ?", (req.email.lower(),))
    user = cursor.fetchone()
    
    if not user:
        user_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO users (id, full_name, email, password, created_at) VALUES (?, ?, ?, ?, ?)",
                       (user_id, req.full_name, req.email.lower(), req.password, datetime.utcnow().isoformat()))
    else:
        user_id = user["id"]
        cursor.execute("UPDATE users SET full_name = ? WHERE id = ?", (req.full_name, user_id))

    token = str(uuid.uuid4())
    expires_at = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
    
    cursor.execute("INSERT INTO sessions (token, user_id, full_name, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                   (token, user_id, req.full_name, datetime.utcnow().isoformat(), expires_at))
    db.commit()
    
    log_audit(db, req.full_name, "GLOBAL", "USER_LOGIN", f"User {req.full_name} logged in successfully.")
    return {"token": token, "full_name": req.full_name, "expires_at": expires_at}

@app.get("/api/branches")
def get_branches(db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT id, name FROM branches ORDER BY created_at ASC")
    return [dict(row) for row in cursor.fetchall()]

@app.post("/api/branches")
def create_branch(req: BranchCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    branch_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("INSERT INTO branches (id, name, created_at) VALUES (?, ?, ?)",
                   (branch_id, req.name, datetime.utcnow().isoformat()))
    db.commit()
    log_audit(db, session["full_name"], branch_id, "CREATE_BRANCH", f"Created branch '{req.name}'")
    return {"id": branch_id, "name": req.name}

@app.put("/api/branches/{branch_id}")
def update_branch(branch_id: str, req: BranchUpdate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("UPDATE branches SET name = ? WHERE id = ?", (req.name, branch_id))
    db.commit()
    log_audit(db, session["full_name"], branch_id, "UPDATE_BRANCH", f"Renamed branch to '{req.name}'")
    return {"status": "success"}

# Module 2: Central Student Database
@app.post("/api/students")
def add_student(req: StudentCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    phone_pattern = re.compile(r"^\+?[0-9]{10,15}$")
    if not phone_pattern.match(req.father_contact.strip()):
        raise HTTPException(status_code=400, detail="Invalid Father's contact number format.")
    if not phone_pattern.match(req.mother_contact.strip()):
        raise HTTPException(status_code=400, detail="Invalid Mother's contact number format.")

    student_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO students (id, branch_id, roll_number, full_name, batch, father_name, father_contact, mother_name, mother_contact, email, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (student_id, req.branch_id, req.roll_number, req.full_name, req.batch, req.father_name, req.father_contact, req.mother_name, req.mother_contact, req.email, datetime.utcnow().isoformat()))
    db.commit()
    
    log_audit(db, session["full_name"], req.branch_id, "ADD_STUDENT", f"Added student {req.full_name} ({req.roll_number})")
    return {"id": student_id, "status": "created"}

@app.get("/api/students")
def list_students(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM students WHERE branch_id = ? ORDER BY roll_number ASC", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

# Module: Fee Department
@app.post("/api/fees")
def add_fee_record(req: FeeRecordCreate, session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    fee_id = str(uuid.uuid4())
    cursor = db.cursor()
    cursor.execute("""
    INSERT INTO fee_records (id, student_id, branch_id, amount_due, due_date, status, updated_at)
    VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
    """, (fee_id, req.student_id, req.branch_id, req.amount_due, req.due_date, datetime.utcnow().isoformat()))
    db.commit()
    
    log_audit(db, session["full_name"], req.branch_id, "ADD_FEE_RECORD", f"Recorded fee due ${req.amount_due} for student ID {req.student_id}")
    return {"id": fee_id, "status": "created"}

@app.get("/api/fees/defaulters")
def list_defaulters(branch_id: str = Query(...), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("""
    SELECT f.id, f.amount_due, f.due_date, f.status, s.full_name as student_name, s.roll_number, s.father_contact, s.mother_contact
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

@app.get("/api/audit-logs")
def get_audit_logs(branch_id: str = Query(...), session: dict = Depends(verify_session), db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT user_name, action, details, timestamp FROM audit_logs WHERE branch_id = ? OR branch_id = 'GLOBAL' ORDER BY timestamp DESC LIMIT 100", (branch_id,))
    return [dict(row) for row in cursor.fetchall()]

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
