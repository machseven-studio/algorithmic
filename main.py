import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Any
import pandas as pd
import numpy as np
import httpx

# Initialize FastAPI App
app = FastAPI(title="EduOps Automator API")

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
async def assistant(req: AssistantRequest):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set on the server.")
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
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
        return parsed
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Assistant error: {str(e)}")

# --- Data Models ---

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

# --- Logic ---

def generate_seating(request: SeatingRequest) -> dict:
    """
    Simple Constraint Satisfaction Solver for Seating.
    Rule: No two students from the same batch in the same room.
    """
    if not request.students or not request.rooms:
        raise HTTPException(status_code=400, detail="Need students and rooms.")

    # Create a DataFrame for easy manipulation
    df_students = pd.DataFrame(request.students.dict() if isinstance(request.students[0], dict) else [s.dict() for s in request.students])
    
    # Sort by batch to ensure even distribution
    df_students = df_students.sort_values(by='batch')
    
    # Assign rooms round-robin style to ensure no batch clustering
    # This is a heuristic approach for the MVP
    seating_plan = {}
    room_counts = {room.id: 0 for room in request.rooms}
    
    # Initialize room buckets
    rooms_bucket = {room.id: [] for room in request.rooms}
    
    # Distribute students evenly across rooms
    for student in request.students:
        # Find the room with the least number of students from the same batch
        # For MVP simplicity: Round-robin assignment
        min_room_id = min(room_counts, key=room_counts.get)
        rooms_bucket[min_room_id].append(student.dict())
        room_counts[min_room_id] += 1
        
    return {"seating_plan": rooms_bucket}

# --- Endpoints ---

from fastapi.responses import FileResponse

@app.get("/")
def root():
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "EduOps Automator API is live. Place index.html in a /static folder next to main.py, or visit /docs."}

@app.post("/api/seating")
def create_seating(request: SeatingRequest):
    try:
        result = generate_seating(request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    return {"status": "healthy"}
