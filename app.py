import os
import io
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy import inspect
import random
import uuid
from datetime import datetime

# --- CONFIGURATION ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./eduops.db")
# For Render, ensure you have a PostgreSQL instance connected. 
# If running locally without DB, it defaults to SQLite above.

# --- DATABASE SETUP ---
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELS ---
class Student(Base):
    __tablename__ = "students"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False)
    batch = Column(String, nullable=False, index=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)

class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, index=True, nullable=False)
    capacity = Column(Integer, nullable=False)

class Teacher(Base):
    __tablename__ = "teachers"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, index=True, nullable=False)
    subject = Column(String, nullable=True)

class Batch(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)

class TimetableSlot(Base):
    __tablename__ = "timetable_slots"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    day = Column(String, nullable=False) # Mon, Tue, etc.
    time_slot = Column(String, nullable=False) # 09:00, 10:00, etc.
    batch_name = Column(String, nullable=False) # Denormalized for easy export

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

# --- SCHEMAS (Pydantic) ---
class StudentCreate(BaseModel):
    name: str
    batch: str
    email: Optional[str] = None
    phone: Optional[str] = None

class RoomCreate(BaseModel):
    name: str
    capacity: int

class TeacherCreate(BaseModel):
    name: str
    subject: Optional[str] = None

class BatchCreate(BaseModel):
    name: str
    description: Optional[str] = None

class SeatingRequest(BaseModel):
    exam_name: str
    students_ids: List[int]
    room_ids: List[int]

class TimetableRequest(BaseModel):
    batch_ids: List[int]
    teacher_ids: List[int]
    room_ids: List[int]
    days: List[str] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    time_slots: List[str] = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]

# --- HELPER: PDF GENERATION ---
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib.units import inch

def generate_pdf(data: dict, filename: str, title: str):
    """Generates a PDF from a dictionary of lists (rows)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=30, bottomMargin=30, leftMargin=30, rightMargin=30)
    
    elements = []
    elements.append(f"{title} - Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Convert data to table format
    if isinstance(data, list) and len(data) > 0:
        headers = list(data[0].keys())
        rows = [list(row.values()) for row in data]
        
        # Add header row
        table_data = [headers] + rows
        
        t = Table(table_data, colWidths=[1.5*inch]*len(headers))
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elements.append(t)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer, filename

# --- DB DEPENDENCY ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- API ROUTES ---
app = FastAPI(title="EduOps Automator")

# 1. CRUD for Students
@app.post("/api/students", summary="Add a student")
def add_student(student: StudentCreate, db: Session = Depends(get_db)):
    db_student = Student(**student.dict())
    db.add(db_student)
    db.commit()
    db.refresh(db_student)
    return {"id": db_student.id, "message": "Student added"}

@app.get("/api/students", summary="Get all students")
def get_students(db: Session = Depends(get_db)):
    return db.query(Student).all()

# 2. CRUD for Rooms
@app.post("/api/rooms", summary="Add a room")
def add_room(room: RoomCreate, db: Session = Depends(get_db)):
    db_room = Room(**room.dict())
    db.add(db_room)
    db.commit()
    db.refresh(db_room)
    return {"id": db_room.id, "message": "Room added"}

@app.get("/api/rooms", summary="Get all rooms")
def get_rooms(db: Session = Depends(get_db)):
    return db.query(Room).all()

# 3. CRUD for Teachers
@app.post("/api/teachers", summary="Add a teacher")
def add_teacher(teacher: TeacherCreate, db: Session = Depends(get_db)):
    db_teacher = Teacher(**teacher.dict())
    db.add(db_teacher)
    db.commit()
    db.refresh(db_teacher)
    return {"id": db_teacher.id, "message": "Teacher added"}

@app.get("/api/teachers", summary="Get all teachers")
def get_teachers(db: Session = Depends(get_db)):
    return db.query(Teacher).all()

# 4. CRUD for Batches
@app.post("/api/batches", summary="Add a batch")
def add_batch(batch: BatchCreate, db: Session = Depends(get_db)):
    db_batch = Batch(**batch.dict())
    db.add(db_batch)
    db.commit()
    db.refresh(db_batch)
    return {"id": db_batch.id, "message": "Batch added"}

@app.get("/api/batches", summary="Get all batches")
def get_batches(db: Session = Depends(get_db)):
    return db.query(Batch).all()

# 5. Seating Logic (with PDF Export)
@app.post("/api/seating/generate", summary="Generate Seating Chart")
def generate_seating(request: SeatingRequest, db: Session = Depends(get_db)):
    # Fetch students and rooms
    students = db.query(Student).filter(Student.id.in_(request.students_ids)).all()
    rooms = db.query(Room).filter(Room.id.in_(request.room_ids)).all()
    
    if not students or not rooms:
        raise HTTPException(status_code=400, detail="Invalid Students or Rooms")

    # Group by batch
    batch_groups = {}
    for s in students:
        if s.batch not in batch_groups:
            batch_groups[s.batch] = []
        batch_groups[s.batch].append(s)

    # Sort rooms by capacity
    sorted_rooms = sorted(rooms, key=lambda r: r.capacity, reverse=True)
    
    # Assign students to rooms
    room_assignments = {r.id: [] for r in sorted_rooms}
    remaining_students = list(students)
    
    for room in sorted_rooms:
        count = 0
        while remaining_students and count < room.capacity:
            room_assignments[room.id].append(remaining_students.pop(0))
            count += 1

    # Shuffle within rooms
    for room_id in room_assignments:
        students_in_room = room_assignments[room_id]
        random.shuffle(students_in_room)
        # Simple adjacent swap fix
        for i in range(len(students_in_room) - 1):
            if students_in_room[i].batch == students_in_room[i+1].batch:
                for j in range(i + 2, len(students_in_room)):
                    if students_in_room[j].batch != students_in_room[i].batch:
                        students_in_room[i], students_in_room[j] = students_in_room[j], students_in_room[i]
                        break

    # Prepare data for PDF
    pdf_data = []
    for room in sorted_rooms:
        for student in room_assignments[room.id]:
            pdf_data.append({
                "Room": room.name,
                "Student Name": student.name,
                "Batch": student.batch,
                "Seat No": len([s for s in room_assignments[room.id] if s.name <= student.name]) # Approx seat no
            })

    # Generate PDF
    buffer, filename = generate_pdf(pdf_data, f"{request.exam_name}_seating.pdf", request.exam_name)
    return {"message": "Seating generated", "pdf_buffer": buffer, "filename": filename, "details": {r.name: [s.name for s in room_assignments[r.id]] for r in sorted_rooms}}

# 6. Timetable Logic (with PDF Export)
@app.post("/api/timetable/generate", summary="Generate Timetable")
def generate_timetable(request: TimetableRequest, db: Session = Depends(get_db)):
    batches = db.query(Batch).filter(Batch.id.in_(request.batch_ids)).all()
    teachers = db.query(Teacher).filter(Teacher.id.in_(request.teacher_ids)).all()
    rooms = db.query(Room).filter(Room.id.in_(request.room_ids)).all()
    
    if not batches or not teachers or not rooms:
        raise HTTPException(status_code=400, detail="Invalid Batches, Teachers, or Rooms")

    # Create a simple grid
    timetable_data = []
    used_teachers = set()
    
    for day in request.days:
        for slot in request.time_slots:
            used_teachers_in_slot = set()
            used_rooms_in_slot = set()
            
            for batch in batches:
                # Find free teacher
                free_teacher = None
                for t in teachers:
                    if t.name not in used_teachers_in_slot:
                        free_teacher = t
                        break
                
                if not free_teacher:
                    continue
                
                # Find free room
                free_room = None
                for r in rooms:
                    if r.name not in used_rooms_in_slot:
                        free_room = r
                        break
                
                if free_room:
                    timetable_data.append({
                        "Day": day,
                        "Time": slot,
                        "Batch": batch.name,
                        "Teacher": free_teacher.name,
                        "Room": free_room.name
                    })
                    used_teachers_in_slot.add(free_teacher.name)
                    used_rooms_in_slot.add(free_room.name)
                    used_teachers.add(free_teacher.name)

    # Store in DB
    for slot in timetable_data:
        batch = next((b for b in batches if b.name == slot["Batch"]), None)
        teacher = next((t for t in teachers if t.name == slot["Teacher"]), None)
        room = next((r for r in rooms if r.name == slot["Room"]), None)
        
        if batch and teacher and room:
            db_slot = TimetableSlot(
                batch_id=batch.id,
                teacher_id=teacher.id,
                room_id=room.id,
                day=slot["Day"],
                time_slot=slot["Time"],
                batch_name=batch.name
            )
            db.add(db_slot)
    
    db.commit()

    # Generate PDF
    buffer, filename = generate_pdf(timetable_data, "timetable.pdf", "Weekly Timetable")
    return {"message": "Timetable generated", "pdf_buffer": buffer, "filename": filename}

# Health Check
@app.get("/", response_class=HTMLResponse)
def read_root():
    return "<h1>EduOps Automator is Live</h1><p>API Endpoints Ready</p>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
