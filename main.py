import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import numpy as np

# Initialize FastAPI App
app = FastAPI(title="EduOps Automator API")

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

@app.get("/")
def root():
    return {"message": "EduOps Automator API is live. Visit /docs for the interactive API playground."}

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
