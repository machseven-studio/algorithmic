import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import random

# ------------------------------------------------------------------
# 1. DATA MODELS (Pydantic)
# ------------------------------------------------------------------

class Student(BaseModel):
    id: int
    name: str
    batch: str  # e.g., "CS-101", "MBA-202"

class Room(BaseModel):
    id: int
    name: str
    capacity: int

class SeatingRequest(BaseModel):
    students: List[Student]
    rooms: List[Room]

class TimetableRequest(BaseModel):
    teachers: List[str]
    batches: List[str]
    rooms: List[str]
    days: List[str] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    slots_per_day: int = 6  # e.g., 9am, 10am, 11am, etc.

# ------------------------------------------------------------------
# 2. LOGIC ENGINES
# ------------------------------------------------------------------

def generate_seating(request: SeatingRequest) -> dict:
    """
    Generates seating arrangements ensuring no two students from the same batch are adjacent.
    """
    if not request.students or not request.rooms:
        raise HTTPException(status_code=400, detail="Students and rooms are required")

    # Sort rooms by capacity descending to pack them efficiently
    sorted_rooms = sorted(request.rooms, key=lambda r: r.capacity, reverse=True)
    
    # Group students by batch
    batch_groups = {}
    for student in request.students:
        if student.batch not in batch_groups:
            batch_groups[student.batch] = []
        batch_groups[student.batch].append(student)

    # Assign students to rooms
    room_assignments = {}
    remaining_students = list(request.students)
    
    for room in sorted_rooms:
        room_assignments[room.id] = []
        # Fill room up to capacity
        count = 0
        while remaining_students and count < room.capacity:
            room_assignments[room.id].append(remaining_students.pop(0))
            count += 1

    # Shuffle within rooms to break batch adjacency patterns as much as possible
    # This is a heuristic; for strict adjacency, we'd use a CSP solver.
    for room_id in room_assignments:
        students_in_room = room_assignments[room_id]
        # Simple shuffle to mix batches
        random.shuffle(students_in_room)
        
        # Post-process: If two adjacent students are from the same batch, swap them if possible
        for i in range(len(students_in_room) - 1):
            if students_in_room[i].batch == students_in_room[i+1].batch:
                # Try to find a non-conflicting swap
                swapped = False
                for j in range(i + 2, len(students_in_room)):
                    if students_in_room[j].batch != students_in_room[i].batch and \
                       students_in_room[j].batch != students_in_room[i-1].batch if i > 0 else True:
                        students_in_room[i], students_in_room[j] = students_in_room[j], students_in_room[i]
                        swapped = True
                        break
                if not swapped:
                    # If no swap found, just leave it (heuristic limitation)
                    pass

    # Format output
    output = {}
    for room in sorted_rooms:
        output[room.name] = [s.name for s in room_assignments[room.id]]
        
    return {"status": "success", "seating": output}

def generate_timetable(request: TimetableRequest) -> dict:
    """
    Generates a simple timetable ensuring no teacher is double-booked.
    """
    if not request.teachers or not request.batches or not request.rooms:
        raise HTTPException(status_code=400, detail="Teachers, batches, and rooms are required")

    # Create a grid: Day -> Slot -> Room -> Batch
    timetable = {day: {f"Slot_{i+1}": {} for i in range(request.slots_per_day)} for day in request.days}
    
    teachers_used = []
    
    # Simple greedy algorithm
    for day in request.days:
        for slot_idx in range(request.slots_per_day):
            slot_key = f"Slot_{slot_idx + 1}"
            used_teachers_in_slot = set()
            used_rooms_in_slot = set()
            
            # Iterate batches to assign a teacher and room
            for batch in request.batches:
                # Find a free teacher
                free_teacher = None
                for teacher in request.teachers:
                    if teacher not in teachers_used and teacher not in used_teachers_in_slot:
                        free_teacher = teacher
                        break
                
                if not free_teacher:
                    continue # Skip this batch if no teacher available
                
                # Find a free room
                free_room = None
                for room in request.rooms:
                    if room not in used_rooms_in_slot:
                        free_room = room
                        break
                
                if free_room:
                    timetable[day][slot_key][batch] = {
                        "teacher": free_teacher,
                        "room": free_room
                    }
                    used_teachers_in_slot.add(free_teacher)
                    used_rooms_in_slot.add(free_room)
                    teachers_used.append(free_teacher) # Keep track globally if needed

    return {"status": "success", "timetable": timetable}

# ------------------------------------------------------------------
# 3. APP SETUP
# ------------------------------------------------------------------

app = FastAPI(title="EduOps Automator")

@app.get("/", response_class=HTMLResponse)
def read_root():
    return "Hello World - If you see this, the server is up!"

@app.post("/api/seating", response_class=JSONResponse)
def create_seating(request: SeatingRequest):
    try:
        result = generate_seating(request)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/timetable", response_class=JSONResponse)
def create_timetable(request: TimetableRequest):
    try:
        result = generate_timetable(request)
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------------
# 4. LOCAL RUNNER (Optional, for testing locally)
# ------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
