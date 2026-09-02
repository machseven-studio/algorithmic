import io
import json
import random
import re
import sqlite3
from typing import Optional, List, Dict
from fastapi import FastAPI, Request, HTTPException, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

app = FastAPI(title="Institute Management System")
security = HTTPBasic()

# Database Setup
def get_conn():
    conn = sqlite3.connect("database.db")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS institutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institute_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (institute_id) REFERENCES institutes(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            batch TEXT NOT NULL,
            roll_number TEXT,
            parent_contact TEXT,
            FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            subject TEXT NOT NULL,
            contact_number TEXT,
            FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS timetables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            batch TEXT NOT NULL,
            schedule_json TEXT NOT NULL,
            FOREIGN KEY (branch_id) REFERENCES branches(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Auth Helpers
def get_current_institute(credentials: HTTPBasicCredentials = Depends(security)):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, username, name FROM institutes WHERE username = ? AND password = ?", 
              (credentials.username, credentials.password))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"id": row[0], "username": row[1], "name": row[2]}

# Timetable Scheduling Logic
class TeacherSpec(BaseModel):
    name: str
    subjects: List[str]  # Separated by commas in UI
    lectures_per_week: int
    unavailable_days: List[str]

def generate_schedule(batch: str, teachers: List[TeacherSpec]):
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    slots = ["Lecture 1", "Lecture 2", "Lecture 3", "Lecture 4"]
    
    # Pool all required lecture tasks: (Teacher, Subject)
    lecture_pool = []
    for t in teachers:
        # If multiple subjects provided via comma, cycle/distribute them across required count
        subs = t.subjects if t.subjects else ["General"]
        for i in range(t.lectures_per_week):
            subj = subs[i % len(subs)]
            lecture_pool.append({"teacher": t.name, "subject": subj, "unavailable": t.unavailable_days})

    # Shuffle pool for random distribution
    random.shuffle(lecture_pool)

    # Initialize grid
    grid = {day: {slot: None for slot in slots} for day in days}
    
    # Track teacher workload per day to avoid double-booking in same slot or overloading
    for item in lecture_pool:
        placed = False
        shuffled_days = days.copy()
        random.shuffle(shuffled_days)
        
        for day in shuffled_days:
            if day in item["unavailable"]:
                continue
            
            # Find an empty slot where this teacher isn't already teaching
            shuffled_slots = slots.copy()
            random.shuffle(shuffled_slots)
            
            for slot in shuffled_slots:
                if grid[day][slot] is None:
                    # Check if teacher already has a class in this slot on this day
                    already_booked = any(
                        grid[day][slot] and grid[day][slot]["teacher"] == item["teacher"] 
                        for s in slots if grid[day][s] is not None
                    )
                    if not already_booked:
                        grid[day][slot] = {"teacher": item["teacher"], "subject": item["subject"]}
                        placed = True
                        break
            if placed:
                break

    return grid

# API Endpoints
@app.post("/api/timetable/generate")
def api_generate_timetable(
    branch_id: int, 
    batch: str, 
    specs: List[TeacherSpec], 
    institute=Depends(get_current_institute)
):
    if not batch:
        raise HTTPException(status_code=400, detail="Batch selection is required.")
    
    # Process comma-separated subjects inside specs
    cleaned_specs = []
    for s in specs:
        # Split subjects by comma if sent as single string
        raw_subs = s.subjects if isinstance(s.subjects, list) else str(s.subjects).split(",")
        parsed_subs = [sub.strip() for sub in raw_subs if sub.strip()]
        cleaned_specs.append(TeacherSpec(
            name=s.name,
            subjects=parsed_subs,
            lectures_per_week=s.lectures_per_week,
            unavailable_days=s.unavailable_days
        ))

    grid = generate_schedule(batch, cleaned_specs)
    
    # Save to database
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO timetables (branch_id, batch, schedule_json) VALUES (?, ?, ?)",
              (branch_id, batch, json.dumps(grid)))
    conn.commit()
    conn.close()
    
    return {"batch": batch, "schedule": grid}

@app.get("/api/timetable/pdf")
def export_timetable_pdf(batch: str, branch_id: int, institute=Depends(get_current_institute)):
    # Basic SVG/HTML render for PDF print stream
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT schedule_json FROM timetables WHERE branch_id = ? AND batch = ? ORDER BY id DESC LIMIT 1", (branch_id, batch))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="No timetable generated for this batch yet.")

    schedule = json.loads(row[0])
    
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Helvetica', sans-serif; padding: 30px; }}
            .header {{ text-align: center; margin-bottom: 30px; border-bottom: 3px solid #000; padding-bottom: 10px; }}
            .inst-name {{ font-size: 32px; font-weight: 900; text-transform: uppercase; letter-spacing: 2px; }}
            .batch-name {{ font-size: 24px; font-weight: 800; color: #d97706; text-transform: uppercase; margin-top: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ border: 1px solid #333; padding: 12px; text-align: center; }}
            th {{ background: #111; color: #fff; text-transform: uppercase; font-size: 12px; }}
            .cell-subject {{ font-weight: bold; font-size: 14px; display: block; }}
            .cell-teacher {{ font-size: 11px; color: #555; display: block; }}
        </style>
    </head>
    <body onload="window.print()">
        <div class="header">
            <div class="inst-name">{institute['name']}</div>
            <div class="batch-name">TIMETABLE - BATCH: {batch}</div>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Day</th>
                    <th>Lecture 1</th>
                    <th>Lecture 2</th>
                    <th>Lecture 3</th>
                    <th>Lecture 4</th>
                </tr>
            </thead>
            <tbody>
    """
    for day, slots in schedule.items():
        html += f"<tr><strong><td>{day}</td></strong>"
        for slot_name in ["Lecture 1", "Lecture 2", "Lecture 3", "Lecture 4"]:
            item = slots.get(slot_name)
            if item:
                html += f"<td><span class='cell-subject'>{item['subject']}</span><span class='cell-teacher'>{item['teacher']}</span></td>"
            else:
                html += "<td>-</td>"
        html += "</tr>"
    html += """
            </tbody>
        </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# Main UI Server
@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_CONTENT

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Academic Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Impact&family=Syne:wght@800;900&display=swap');
        
        /* High-Impact Uppercase Headers for Homepage */
        .ultra-bold-header {
            font-family: 'Syne', 'Impact', sans-serif;
            font-weight: 900;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
    </style>
</head>
<body class="bg-stone-950 text-stone-100 min-h-screen">
    
    <!-- HOMEPAGE HEADER SECTION -->
    <header class="border-b border-amber-500/20 bg-stone-900/50 p-6 backdrop-blur">
        <div class="max-w-7xl mx-auto flex justify-between items-center">
            <div>
                <h1 id="institute-display" class="ultra-bold-header text-4xl text-amber-500 tracking-wider">
                    ACADEMIC INSTITUTE
                </h1>
                <p id="welcome-display" class="ultra-bold-header text-xl text-stone-300 mt-1">
                    WELCOME, ADMIN
                </p>
            </div>
            <div class="space-x-4">
                <button onclick="switchTab('home')" class="px-4 py-2 text-sm font-bold uppercase tracking-wider text-amber-500 hover:text-amber-400">Home</button>
                <button onclick="switchTab('timetable')" class="px-4 py-2 text-sm font-bold uppercase tracking-wider bg-amber-500 text-stone-950 rounded hover:bg-amber-400">Timetable Module</button>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto p-6">
        
        <!-- TIMETABLE MODULE UI -->
        <section id="timetable-module" class="space-y-6">
            <div class="bg-stone-900 border border-amber-500/20 p-6 rounded-xl space-y-4">
                <h2 class="text-2xl font-bold text-amber-500 uppercase tracking-wide">Generate Class Timetable</h2>
                
                <!-- BATCH SELECTOR -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs font-bold uppercase tracking-wider text-stone-400 mb-2">Target Batch</label>
                        <select id="timetable-batch-select" class="w-full bg-stone-950 border border-stone-800 rounded p-3 text-amber-400 font-bold focus:border-amber-500 outline-none">
                            <option value="">-- SELECT BATCH --</option>
                            <option value="Batch A">Batch A</option>
                            <option value="Batch B">Batch B</option>
                            <option value="Batch C">Batch C</option>
                        </select>
                    </div>
                </div>

                <!-- TEACHER SPECIFICATIONS INPUT -->
                <div class="border-t border-stone-800 pt-4">
                    <h3 class="text-sm font-bold uppercase tracking-wider text-stone-300 mb-3">Teacher Rules & Constraints</h3>
                    <div id="teacher-rules-container" class="space-y-3">
                        <div class="teacher-rule-row grid grid-cols-1 md:grid-cols-4 gap-3 bg-stone-950 p-3 rounded border border-stone-800">
                            <input type="text" placeholder="Teacher Name (e.g. Dr. Smith)" class="t-name bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200">
                            <input type="text" placeholder="Subjects (Comma Separated: Math, Physics)" class="t-subjects bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200">
                            <input type="number" placeholder="Lectures / Week" class="t-count bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200" min="1" max="10">
                            <input type="text" placeholder="Unavailable Days (e.g. Friday, Saturday)" class="t-unavail bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200">
                        </div>
                    </div>
                    <button onclick="addTeacherRow()" class="mt-3 text-xs font-bold uppercase text-amber-500 hover:text-amber-400">+ Add Another Teacher</button>
                </div>

                <!-- ACTION BUTTONS -->
                <div class="flex gap-4 pt-4 border-t border-stone-800">
                    <button onclick="generateTimetable()" class="px-6 py-3 bg-amber-500 text-stone-950 font-bold text-sm uppercase tracking-wider rounded hover:bg-amber-400">
                        Generate Timetable
                    </button>
                    <button onclick="generateTimetable()" class="px-6 py-3 border border-amber-500/40 text-amber-500 font-bold text-sm uppercase tracking-wider rounded hover:bg-amber-500/10">
                        🔄 Regenerate / Shuffle Slots
                    </button>
                    <button onclick="downloadPDF()" class="px-6 py-3 bg-stone-800 text-stone-200 font-bold text-sm uppercase tracking-wider rounded hover:bg-stone-700">
                        📄 Download PDF
                    </button>
                </div>
            </div>

            <!-- TIMETABLE DISPLAY GRID -->
            <div id="timetable-output" class="hidden bg-stone-900 border border-amber-500/20 p-6 rounded-xl">
                <div class="flex justify-between items-center mb-4">
                    <h3 id="display-batch-title" class="ultra-bold-header text-2xl text-amber-500"></h3>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-stone-800 text-xs font-bold uppercase text-stone-400 bg-stone-950">
                                <th class="p-3">Day</th>
                                <th class="p-3">Lecture 1</th>
                                <th class="p-3">Lecture 2</th>
                                <th class="p-3">Lecture 3</th>
                                <th class="p-3">Lecture 4</th>
                            </tr>
                        </thead>
                        <tbody id="timetable-grid-body" class="divide-y divide-stone-800 text-sm">
                        </tbody>
                    </table>
                </div>
            </div>
        </section>
    </main>

    <script>
        function addTeacherRow() {
            const container = document.getElementById('teacher-rules-container');
            const row = document.createElement('div');
            row.className = 'teacher-rule-row grid grid-cols-1 md:grid-cols-4 gap-3 bg-stone-950 p-3 rounded border border-stone-800';
            row.innerHTML = `
                <input type="text" placeholder="Teacher Name" class="t-name bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200">
                <input type="text" placeholder="Subjects (Comma Separated)" class="t-subjects bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200">
                <input type="number" placeholder="Lectures / Week" class="t-count bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200" min="1" max="10">
                <input type="text" placeholder="Unavailable Days" class="t-unavail bg-stone-900 border border-stone-800 p-2 rounded text-sm text-stone-200">
            `;
            container.appendChild(row);
        }

        async function generateTimetable() {
            const batch = document.getElementById('timetable-batch-select').value;
            if (!batch) {
                alert('Please select a batch first!');
                return;
            }

            const rows = document.querySelectorAll('.teacher-rule-row');
            const specs = [];

            rows.forEach(r => {
                const name = r.querySelector('.t-name').value.trim();
                const subjectsRaw = r.querySelector('.t-subjects').value.trim();
                const count = parseInt(r.querySelector('.t-count').value) || 0;
                const unavailRaw = r.querySelector('.t-unavail').value.trim();

                if (name && count > 0) {
                    specs.push({
                        name: name,
                        subjects: subjectsRaw.split(',').map(s => s.trim()).filter(Boolean),
                        lectures_per_week: count,
                        unavailable_days: unavailRaw.split(',').map(d => d.trim()).filter(Boolean)
                    });
                }
            });

            if (specs.length === 0) {
                alert('Please enter at least one valid teacher specification.');
                return;
            }

            const res = await fetch('/api/timetable/generate?branch_id=1&batch=' + encodeURIComponent(batch), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(specs)
            });

            if (res.ok) {
                const data = await res.json();
                renderTimetable(data.batch, data.schedule);
            } else {
                const err = await res.json();
                alert(err.detail || 'Failed to generate timetable.');
            }
        }

        function renderTimetable(batch, schedule) {
            document.getElementById('timetable-output').classList.remove('hidden');
            document.getElementById('display-batch-title').innerText = 'TIMETABLE FOR ' + batch.toUpperCase();

            const tbody = document.getElementById('timetable-grid-body');
            tbody.innerHTML = '';

            const slots = ["Lecture 1", "Lecture 2", "Lecture 3", "Lecture 4"];

            for (const [day, daySlots] of Object.entries(schedule)) {
                let tr = document.createElement('tr');
                let html = `<td class="p-3 font-bold text-amber-400">${day}</td>`;
                
                slots.forEach(s => {
                    const item = daySlots[s];
                    if (item) {
                        html += `<td class="p-3"><div class="font-bold text-stone-100">${item.subject}</div><div class="text-xs text-stone-400">${item.teacher}</div></td>`;
                    } else {
                        html += `<td class="p-3 text-stone-600">-</td>`;
                    }
                });

                tr.innerHTML = html;
                tbody.appendChild(tr);
            }
        }

        function downloadPDF() {
            const batch = document.getElementById('timetable-batch-select').value;
            if (!batch) {
                alert('Select a batch first.');
                return;
            }
            window.open(`/api/timetable/pdf?branch_id=1&batch=${encodeURIComponent(batch)}`, '_blank');
        }
    </script>
</body>
</html>
"""
