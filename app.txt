import os
import random
import sqlite3
from flask import Flask, render_template_string, request, jsonify
from datetime import datetime

# --- CONFIGURATION ---
APP_PORT = int(os.environ.get('PORT', 5000))
DB_NAME = 'eduops.db'

app = Flask(__name__)

# --- DATABASE SETUP ---
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            teacher_id INTEGER,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            capacity INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            batch_id INTEGER,
            FOREIGN KEY (batch_id) REFERENCES batches(id)
        );
        CREATE TABLE IF NOT EXISTS schedule_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER,
            subject_id INTEGER,
            room_id INTEGER,
            day TEXT,
            time_slot TEXT,
            FOREIGN KEY (teacher_id) REFERENCES teachers(id),
            FOREIGN KEY (subject_id) REFERENCES subjects(id),
            FOREIGN KEY (room_id) REFERENCES rooms(id)
        );
    ''')
    conn.commit()
    conn.close()

def seed_data():
    """Populates DB if empty"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT count(*) FROM batches")
    if c.fetchone()[0] == 0:
        # Create Batches
        b1 = c.execute("INSERT INTO batches (name) VALUES (?)", ("Engineering Batch A",)).lastrowid
        b2 = c.execute("INSERT INTO batches (name) VALUES (?)", ("Engineering Batch B",)).lastrowid
        b3 = c.execute("INSERT INTO batches (name) VALUES (?)", ("Management Batch C",)).lastrowid
        
        # Create Rooms
        r1 = c.execute("INSERT INTO rooms (name, capacity) VALUES (?, ?)", ("Hall A", 50)).lastrowid
        r2 = c.execute("INSERT INTO rooms (name, capacity) VALUES (?, ?)", ("Hall B", 50)).lastrowid
        r3 = c.execute("INSERT INTO rooms (name, capacity) VALUES (?, ?)", ("Class 1", 30)).lastrowid
        
        # Create Teachers
        t1 = c.execute("INSERT INTO teachers (name) VALUES (?)", ("Dr. Smith",)).lastrowid
        t2 = c.execute("INSERT INTO teachers (name) VALUES (?)", ("Prof. Jones",)).lastrowid
        
        # Create Subjects
        s1 = c.execute("INSERT INTO subjects (name, teacher_id) VALUES (?, ?)", ("Math", t1)).lastrowid
        s2 = c.execute("INSERT INTO subjects (name, teacher_id) VALUES (?, ?)", ("Physics", t2)).lastrowid
        
        # Create Students
        for i in range(20):
            c.execute("INSERT INTO students (name, batch_id) VALUES (?, ?)", (f"Eng Student {i+1}", b1))
            c.execute("INSERT INTO students (name, batch_id) VALUES (?, ?)", (f"Eng Student {i+20}", b2))
            c.execute("INSERT INTO students (name, batch_id) VALUES (?, ?)", (f"Mgmt Student {i+1}", b3))
            
        conn.commit()
    conn.close()

# Initialize DB on startup
init_db()
seed_data()

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EduOps Automator</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; }
        .navbar { margin-bottom: 20px; }
        .card { margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .btn-nav { margin-right: 10px; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">EduOps Automator</a>
            <div class="navbar-nav">
                <a class="nav-link" href="/">Home</a>
                <a class="nav-link" href="/generate-seating">Seating</a>
                <a class="nav-link" href="/generate-timetable">Timetable</a>
            </div>
        </div>
    </nav>
    <div class="container">
        {% block content %}{% endblock %}
    </div>
</body>
</html>
"""

HOME_TEMPLATE = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="mt-5 text-center">
    <h1>Welcome to EduOps</h1>
    <p class="lead">Automate your institute's clerical work.</p>
    <div class="row justify-content-center">
        <div class="col-md-4">
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">Generate Seating</h5>
                    <p class="card-text">Eliminate cheating with smart seating arrangements.</p>
                    <a href="/generate-seating" class="btn btn-primary w-100">Go to Seating</a>
                </div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">Generate Timetable</h5>
                    <p class="card-text">Optimize teacher and room schedules.</p>
                    <a href="/generate-timetable" class="btn btn-primary w-100">Go to Timetable</a>
                </div>
            </div>
        </div>
    </div>
</div>
""")

SEATING_TEMPLATE = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="mt-5">
    <h1>Generate Seating Arrangement</h1>
    <button id="generateBtn" class="btn btn-success mt-3">Generate Seats</button>
    <div id="output" class="mt-4"></div>
</div>
<script>
    document.getElementById('generateBtn').addEventListener('click', async () => {
        const btn = document.getElementById('generateBtn');
        btn.disabled = true;
        btn.innerText = "Generating...";
        
        const response = await fetch('/api/generate-seating', { method: 'POST' });
        const data = await response.json();
        
        if (data.success) {
            let html = '';
            for (const [roomId, studentIds] of Object.entries(data.plan)) {
                html += `<div class="card"><div class="card-header">Room ID: ${roomId}</div><div class="card-body"><ul>`;
                studentIds.forEach(id => {
                    html += `<li>Student ID: ${id}</li>`;
                });
                html += `</ul></div></div>`;
            }
            document.getElementById('output').innerHTML = html;
        } else {
            document.getElementById('output').innerText = "Error generating seating.";
        }
        btn.disabled = false;
        btn.innerText = "Generate Seats";
    });
</script>
""")

TIMETABLE_TEMPLATE = BASE_HTML.replace("{% block content %}{% endblock %}", """
<div class="mt-5">
    <h1>Generate Timetable</h1>
    <button id="generateBtn" class="btn btn-success mt-3">Generate Schedule</button>
    <div id="output" class="mt-4"></div>
</div>
<script>
    document.getElementById('generateBtn').addEventListener('click', async () => {
        const btn = document.getElementById('generateBtn');
        btn.disabled = true;
        btn.innerText = "Generating...";
        
        const response = await fetch('/api/generate-timetable', { method: 'POST' });
        const data = await response.json();
        
        if (data.success) {
            let html = '<table class="table table-bordered table-striped"><thead><tr><th>Teacher</th><th>Subject</th><th>Room</th><th>Day</th><th>Time</th></tr></thead><tbody>';
            data.schedule.forEach(entry => {
                html += `<tr><td>${entry.teacher_id}</td><td>${entry.subject_id}</td><td>${entry.room_id}</td><td>${entry.day}</td><td>${entry.time_slot}</td></tr>`;
            });
            html += '</tbody></table>';
            document.getElementById('output').innerHTML = html;
        } else {
            document.getElementById('output').innerText = "Error generating timetable.";
        }
        btn.disabled = false;
        btn.innerText = "Generate Schedule";
    });
</script>
""")

# --- ROUTES ---

@app.route('/')
def index():
    return render_template_string(HOME_TEMPLATE)

@app.route('/generate-seating')
def seating_page():
    return render_template_string(SEATING_TEMPLATE)

@app.route('/generate-timetable')
def timetable_page():
    return render_template_string(TIMETABLE_TEMPLATE)

@app.route('/api/generate-seating', methods=['POST'])
def api_generate_seating():
    conn = get_db()
    rooms = conn.execute("SELECT id, name FROM rooms").fetchall()
    students = conn.execute("SELECT id FROM students").fetchall()
    conn.close()
    
    # Shuffle students
    student_ids = [s['id'] for s in students]
    random.shuffle(student_ids)
    
    # Distribute students to rooms
    seating_plan = {}
    for room in rooms:
        seating_plan[room['id']] = []
    
    for i, sid in enumerate(student_ids):
        room_id = rooms[i % len(rooms)]['id']
        seating_plan[room_id].append(sid)
        
    return jsonify({"success": True, "plan": seating_plan})

@app.route('/api/generate-timetable', methods=['POST'])
def api_generate_timetable():
    conn = get_db()
    teachers = conn.execute("SELECT id, name FROM teachers").fetchall()
    subjects = conn.execute("SELECT id, name FROM subjects").fetchall()
    rooms = conn.execute("SELECT id, name FROM rooms").fetchall()
    conn.close()
    
    schedule = []
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    time_slots = ["9:00-10:00", "10:00-11:00", "11:00-12:00", "13:00-14:00", "14:00-15:00"]
    
    for day in days:
        for slot in time_slots:
            for teacher in teachers:
                subject = random.choice(subjects)
                room = random.choice(rooms)
                schedule.append({
                    "teacher_id": teacher['id'],
                    "subject_id": subject['id'],
                    "room_id": room['id'],
                    "day": day,
                    "time_slot": slot
                })
                
    return jsonify({"success": True, "schedule": schedule})

# --- MAIN ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=APP_PORT, debug=True)
