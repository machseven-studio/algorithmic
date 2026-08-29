import os
import json
import sqlite3
import secrets
import hashlib
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Any
import pandas as pd
import numpy as np
import httpx

app = FastAPI(title="Algorithmic API")

DB_PATH = os.path.join(os.path.dirname(__file__), "algorithmic.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
            created_at TEXT NOT NULL
        )
    """)
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
    token = secrets.token_hex(32)
    conn.execute("INSERT INTO sessions (token, institute_id, created_at) VALUES (?, ?, ?)",
                 (token, institute_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return {"token": token, "institute_name": req.institute_name.strip()}

@app.post("/api/login")
def login(req: LoginRequest):
    conn = get_db()
    row = conn.execute("SELECT * FROM institutes WHERE email = ?", (req.email.lower().strip(),)).fetchone()
    if not row or hash_password(req.password, row["salt"]) != row["password_hash"]:
        conn.close()
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    token = secrets.token_hex(32)
    conn.execute("INSERT INTO sessions (token, institute_id, created_at) VALUES (?, ?, ?)",
                 (token, row["id"], datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return {"token": token, "institute_name": row["institute_name"]}

def get_current_institute(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in.")
    token = authorization.split(" ", 1)[1]
    conn = get_db()
    row = conn.execute("""
        SELECT institutes.id as id, institutes.institute_name as institute_name
        FROM sessions JOIN institutes ON sessions.institute_id = institutes.id
        WHERE sessions.token = ?
    """, (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

ASSISTANT_SYSTEM_PROMPT = """You are the in-app assistant for an education institute's operations tool.
You control exactly two things: a classroom seating grid and a weekly class timetable.
Your only job is to make requested changes to one or both of those, based on the current state given to you.
If the request has nothing to do with seating or timetabling, politely say that's outside what you can do here, and change nothing.
Respond with ONLY a JSON object, no markdown fences, no commentary outside the JSON, in this exact shape:
{"reply": "short plain-language explanation of what you did or why you couldn't", "seatGrid": <updated seat grid array, or null if unchanged>, "schedule": <updated schedule object, or null if unchanged>}
Preserve the existing data structure shapes exactly when you modify them - only change the specific cells relevant to the request.
"""

class AssistantRequest(BaseModel):
    message: str
    seatGrid: Optional[Any] = None
    schedule: Optional[Any] = None

@app.post("/api/assistant")
async def assistant(req: AssistantRequest, authorization: Optional[str] = Header(None)):
    get_current_institute(authorization)
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set on the server. Add it in Render's Environment tab.")
    current_state = {"seatGrid": req.seatGrid, "schedule": req.schedule}
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

class Student(BaseModel):
    id: str
    name: str
    batch: str

class Room(BaseModel):
    id: str
    capacity: int

class SeatingRequest(BaseModel):
    students: List[Student]
    rooms: List[Room]

def generate_seating(request: SeatingRequest) -> dict:
    if not request.students or not request.rooms:
        raise HTTPException(status_code=400, detail="Need students and rooms.")
    df_students = pd.DataFrame([s.dict() for s in request.students])
    df_students = df_students.sort_values(by='batch')
    room_counts = {room.id: 0 for room in request.rooms}
    rooms_bucket = {room.id: [] for room in request.rooms}
    for student in request.students:
        min_room_id = min(room_counts, key=room_counts.get)
        rooms_bucket[min_room_id].append(student.dict())
        room_counts[min_room_id] += 1
    return {"seating_plan": rooms_bucket}

@app.get("/")
def root():
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Algorithmic API is live. Place index.html in a /static folder next to main.py, or visit /docs."}

@app.post("/api/seating")
def create_seating(request: SeatingRequest, authorization: Optional[str] = Header(None)):
    get_current_institute(authorization)
    try:
        result = generate_seating(request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "healthy"}
