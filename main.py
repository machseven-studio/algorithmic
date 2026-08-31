# main.py
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import sqlite3
import os
import shutil
import json

app = FastAPI(title="ALGORITHMIC", version="3.0.0")

DB_FILE = "algorithmic_enterprise.db"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            name TEXT,
            email TEXT,
            course TEXT,
            status TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            name TEXT,
            subject TEXT,
            department TEXT,
            document TEXT,
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
            semester TEXT,
            units INTEGER,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            student_name TEXT,
            date TEXT,
            status TEXT,
            document TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS timetables_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            batch_name TEXT,
            config_json TEXT,
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
    cursor.execute("SELECT COUNT(*) FROM branches")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO branches (name) VALUES ('Main Campus - Mumbai')")
    conn.commit()
    conn.close()

init_db()

class BranchCreate(BaseModel):
    name: str

@app.get("/api/branches")
def get_branches():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM branches")
    branches = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return branches

@app.post("/api/branches")
def add_branch(branch: BranchCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO branches (name) VALUES (?)", (branch.name,))
        conn.commit()
        branch_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Branch already exists")
    conn.close()
    return {"id": branch_id, "name": branch.name}

@app.get("/api/records/{module}/{branch_id}")
def get_records(module: str, branch_id: int):
    valid_modules = ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'invigilation', 'fees']
    if module not in valid_modules:
        raise HTTPException(status_code=400, detail="Invalid module")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {module} WHERE branch_id = ?", (branch_id,))
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return records

@app.post("/api/records/{module}")
async def add_record(
    module: str,
    branch_id: int = Form(...),
    data_json: str = Form(...),
    file: UploadFile = File(None)
):
    valid_modules = ['students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'invigilation', 'fees']
    if module not in valid_modules:
        raise HTTPException(status_code=400, detail="Invalid module")
    
    data = json.loads(data_json)
    doc_filename = None
    if file:
        doc_filename = f"{os.urandom(6).hex()}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, doc_filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    if module == 'students':
        cursor.execute("INSERT INTO students (branch_id, name, email, course, status, document) VALUES (?, ?, ?, ?, ?, ?)",
                       (branch_id, data.get('name'), data.get('email'), data.get('course'), data.get('status', 'Active'), doc_filename))
    elif module == 'teachers':
        cursor.execute("INSERT INTO teachers (branch_id, name, subject, department, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('name'), data.get('subject'), data.get('department'), doc_filename))
    elif module == 'classrooms':
        cursor.execute("INSERT INTO classrooms (branch_id, room_no, capacity, building, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('room_no'), data.get('capacity'), data.get('building'), doc_filename))
    elif module == 'syllabus':
        cursor.execute("INSERT INTO syllabus (branch_id, subject, semester, units, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('subject'), data.get('semester'), data.get('units'), doc_filename))
    elif module == 'attendance':
        cursor.execute("INSERT INTO attendance (branch_id, student_name, date, status, document) VALUES (?, ?, ?, ?, ?)",
                       (branch_id, data.get('student_name'), data.get('date'), data.get('status'), doc_filename))
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

@app.get("/api/timetable/slots/{branch_id}")
def get_timetable_slots(branch_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM timetables_slots WHERE branch_id = ?", (branch_id,))
    slots = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return slots

class TimetableGenerateRequest(BaseModel):
    branch_id: int
    batch_name: str
    teachers_config: list # [{name, subject, lectures_per_week, unavailable_days: []}]
    timings: list # ["09:00 AM - 10:00 AM", ...]

@app.post("/api/timetable/generate")
def generate_timetable(req: TimetableGenerateRequest):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Clear old slots for this batch in this branch
    cursor.execute("DELETE FROM timetables_slots WHERE branch_id = ? AND batch_name = ?", (req.branch_id, req.batch_name))
    
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    generated_slots = []
    
    # Simple algorithmic schedule generator obeying unavailable days and lecture limits
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
                # Check if slot already taken for this batch & time & day
                cursor.execute("""
                    SELECT COUNT(*) FROM timetables_slots 
                    WHERE branch_id = ? AND batch_name = ? AND day = ? AND time_slot = ?
                """, (req.branch_id, req.batch_name, day, slot_time))
                if cursor.fetchone()[0] == 0:
                    room = "Lecture Hall 101"
                    cursor.execute("""
                        INSERT INTO timetables_slots (branch_id, batch_name, day, time_slot, subject, teacher, room)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (req.branch_id, req.batch_name, day, slot_time, subject, teacher_name, room))
                    generated_slots.append({"day": day, "time_slot": slot_time, "subject": subject, "teacher": teacher_name, "room": room})
                    assigned_count += 1
                    
    conn.commit()
    conn.close()
    return {"status": "success", "slots": generated_slots}

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
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Playfair+Display:ital,wght@0,600;1,600&display=swap');
        
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #070707;
            background-image: 
                radial-gradient(rgba(212, 175, 55, 0.05) 1.5px, transparent 1.5px),
                radial-gradient(rgba(212, 175, 55, 0.02) 1.5px, #070707 1.5px);
            background-size: 40px 40px;
            background-position: 0 0, 20px 20px;
            color: #f3f4f6;
            overflow-x: hidden;
        }

        .elegant-font {
            font-family: 'Playfair Display', serif;
        }

        .gold-gradient-text {
            background: linear-gradient(135deg, #BF953F 0%, #FCF6BA 25%, #B38728 50%, #FBF5B7 75%, #AA771C 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .gold-border {
            border-color: rgba(212, 175, 55, 0.22);
        }

        .gold-border-glow:focus, .gold-border-glow:hover {
            border-color: #D4AF37;
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.15);
        }

        .gold-bg {
            background: linear-gradient(135deg, #D4AF37, #AA771C);
        }

        .glass-panel {
            background: rgba(13, 13, 13, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(212, 175, 55, 0.15);
        }

        .sidebar-item {
            transition: all 0.2s ease;
            letter-spacing: 0.06em;
        }
        .sidebar-item:hover, .sidebar-item.active {
            background: rgba(212, 175, 55, 0.1);
            color: #D4AF37;
            border-left: 3px solid #D4AF37;
            padding-left: 1.75rem;
        }

        /* Fast, snappiness optimized transitions */
        .fast-transition {
            transition: all 0.15s ease-in-out;
        }

        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: #0a0a0a; }
        ::-webkit-scrollbar-thumb { background: #332d16; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #D4AF37; }
    </style>
</head>
<body class="min-h-screen flex flex-col">

    <!-- LOGIN SCREEN OVERLAY -->
    <div id="loginOverlay" class="fixed inset-0 z-50 bg-[#050505] flex items-center justify-center p-4">
        <div class="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(212,175,55,0.06)_0,transparent_70%)]"></div>
        <div class="glass-panel w-full max-w-md p-8 rounded-2xl shadow-2xl relative z-10 border gold-border">
            <div class="text-center mb-8">
                <div class="inline-block p-3 rounded-full bg-[#121212] border gold-border mb-4 shadow-lg">
                    <span class="text-2xl font-black gold-gradient-text">⚡</span>
                </div>
                <h1 class="text-2xl font-black gold-gradient-text tracking-wider">ALGORITHMIC</h1>
                <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Enterprise Institutional Portal</p>
            </div>
            <form onsubmit="handleLogin(event)" class="space-y-5">
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Executive ID / Email</label>
                    <input type="text" id="loginEmail" required placeholder="admin@machsevenstudio.com" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Secure Passcode</label>
                    <input type="password" id="loginPassword" required placeholder="••••••••••••" class="w-full bg-[#0c0c0c] border gold-border rounded-xl px-4 py-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
                <button type="submit" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3.5 rounded-xl text-sm fast-transition shadow-lg tracking-wider uppercase">
                    Initialize Secure Session
                </button>
            </form>
            <div class="mt-8 pt-6 border-t gold-border text-center text-xs text-gray-500 space-y-1">
                <p class="font-semibold text-gray-400">CREATED BY SAMARTH DAVE</p>
                <p>FOUNDER OF <a href="https://machsevenstudios-website.onrender.com" target="_blank" class="gold-gradient-text hover:underline font-semibold">MACHSEVENSTUDIOS</a></p>
                <p class="text-[10px] text-yellow-600/80 tracking-widest uppercase pt-1">POWERED BY METASYS<sup>®</sup></p>
            </div>
        </div>
    </div>

    <!-- MAIN APP CONTAINER -->
    <div id="appContainer" class="min-h-screen flex flex-col hidden">
        <!-- Top Persistent Header -->
        <header class="border-b gold-border bg-[#0a0a0a]/95 backdrop-blur-md px-8 py-4 flex justify-between items-center sticky top-0 z-40">
            <div class="flex items-center space-x-6">
                <h1 class="text-xl font-black gold-gradient-text tracking-wider">ALGORITHMIC</h1>
                <div class="h-5 w-[1px] bg-yellow-600/30"></div>
                <!-- Institute Name displayed on top at all times -->
                <div class="flex items-center space-x-2">
                    <span class="text-xs uppercase tracking-widest text-gray-400">Institute:</span>
                    <span id="headerInstituteName" class="text-sm font-bold text-gray-200 tracking-wide bg-[#141414] px-3 py-1 rounded-lg border gold-border">ALGORITHMIC ACADEMY OF EXCELLENCE</span>
                </div>
            </div>
            <div class="flex items-center space-x-6 text-sm">
                <div class="flex items-center space-x-2 bg-[#121212] px-3 py-1.5 rounded-lg border gold-border">
                    <span class="text-xs text-gray-400">Active Branch:</span>
                    <select id="branchSelector" class="bg-transparent text-sm font-semibold gold-gradient-text focus:outline-none cursor-pointer">
                        <!-- Populated dynamically -->
                    </select>
                    <button onclick="openAddBranchModal()" class="ml-2 text-xs bg-[#1a1a1a] hover:bg-[#252525] gold-gradient-text border gold-border px-2 py-0.5 rounded fast-transition">+ Branch</button>
                </div>
                <div class="text-xs text-right border-l pl-6 gold-border">
                    <div class="text-gray-400">Logged in as</div>
                    <div class="font-bold gold-gradient-text">Samarth Dave (<a href="https://machsevenstudios-website.onrender.com" target="_blank" class="hover:underline">MachSevenStudios</a>)</div>
                </div>
                <button onclick="handleLogout()" class="text-xs bg-[#161616] hover:bg-[#222] text-red-400 border border-red-900/40 px-3 py-2 rounded-lg fast-transition">Logout</button>
            </div>
        </header>

        <!-- Main Workspace Body -->
        <div class="flex flex-1 overflow-hidden">
            <!-- Sidebar Navigation -->
            <nav class="w-72 border-r gold-border bg-[#0b0b0b] flex flex-col py-6 space-y-1.5 shrink-0">
                <div class="px-6 pb-2 text-[11px] font-bold text-gray-500 uppercase tracking-widest">Enterprise Modules</div>
                
                <button onclick="switchModule('home')" class="sidebar-item active w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>⚡</span> <span>Home Dashboard</span>
                </button>
                <button onclick="switchModule('students')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>🎓</span> <span>Students</span>
                </button>
                <button onclick="switchModule('teachers')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>👨‍🏫</span> <span>Teachers</span>
                </button>
                <button onclick="switchModule('classrooms')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>🏛️</span> <span>Classrooms</span>
                </button>
                <button onclick="switchModule('syllabus')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>📚</span> <span>Syllabus</span>
                </button>
                <button onclick="switchModule('attendance')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>📋</span> <span>Attendance</span>
                </button>
                <button onclick="switchModule('timetables')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>🕒</span> <span>Timetable</span>
                </button>
                <button onclick="switchModule('invigilation')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>🛡️</span> <span>Invigilator Duty</span>
                </button>
                <button onclick="switchModule('fees')" class="sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3">
                    <span>💳</span> <span>Fees (INR ₹)</span>
                </button>
                
                <!-- Bottom Left Branding -->
                <div class="mt-auto px-6 pt-6 border-t gold-border text-[11px] text-gray-400 space-y-1 bg-[#090909]">
                    <p class="elegant-font text-sm gold-gradient-text tracking-wide">created by Samarth Dave</p>
                    <p class="text-gray-300">Founder of <a href="https://machsevenstudios-website.onrender.com" target="_blank" class="gold-gradient-text hover:underline">MachSevenStudios</a></p>
                    <p class="text-[10px] text-yellow-600 font-bold uppercase tracking-widest pt-1">Powered by Metasys<sup>®</sup></p>
                </div>
            </nav>

            <!-- Dynamic Content Viewport -->
            <main class="flex-1 p-10 overflow-y-auto bg-[#070707]" id="mainContent">
                <!-- Content injected dynamically -->
            </main>
        </div>
    </div>

    <!-- Generic Add Record Modal with Document Support -->
    <div id="recordModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-lg shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h3 id="modalTitle" class="text-lg font-extrabold gold-gradient-text uppercase tracking-wider">Add Record</h3>
                <button onclick="closeRecordModal()" class="text-gray-400 hover:text-white text-lg font-bold">✕</button>
            </div>
            <form id="recordForm" onsubmit="submitRecordForm(event)" class="space-y-4">
                <div id="modalFields" class="space-y-4">
                    <!-- Dynamic fields populated here -->
                </div>
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Attach Document (PDF/Scan/File)</label>
                    <input type="file" id="recordFile" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-2.5 text-xs text-gray-300 file:mr-4 file:py-1 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-[#221c0c] file:text-yellow-500 hover:file:bg-[#332a0f]">
                </div>
                <div class="flex justify-end space-x-3 pt-4 border-t gold-border">
                    <button type="button" onclick="closeRecordModal()" class="px-5 py-2.5 text-xs font-bold uppercase bg-gray-900 hover:bg-gray-800 text-gray-300 rounded-xl fast-transition">Cancel</button>
                    <button type="submit" class="px-6 py-2.5 text-xs font-extrabold uppercase gold-bg hover:opacity-95 text-black rounded-xl fast-transition shadow-lg">Save Record</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Add Branch Modal -->
    <div id="branchModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center hidden z-50">
        <div class="glass-panel border gold-border p-8 rounded-2xl w-full max-w-md shadow-2xl">
            <h3 class="text-lg font-extrabold gold-gradient-text mb-4 uppercase tracking-wider">Create New Branch</h3>
            <input type="text" id="newBranchName" placeholder="Branch Name (e.g. South Campus)" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 mb-6 gold-border-glow focus:outline-none">
            <div class="flex justify-end space-x-3">
                <button onclick="closeAddBranchModal()" class="px-5 py-2.5 text-xs font-bold uppercase bg-gray-900 hover:bg-gray-800 text-gray-300 rounded-xl fast-transition">Cancel</button>
                <button onclick="createNewBranch()" class="px-6 py-2.5 text-xs font-extrabold uppercase gold-bg hover:opacity-95 text-black rounded-xl fast-transition shadow-lg">Create</button>
            </div>
        </div>
    </div>

    <script>
        let branches = [];
        let currentBranchId = null;
        let currentModule = 'home';

        function handleLogin(e) {
            e.preventDefault();
            document.getElementById('loginOverlay').classList.add('hidden');
            document.getElementById('appContainer').classList.remove('hidden');
            initApp();
        }

        function handleLogout() {
            document.getElementById('appContainer').classList.add('hidden');
            document.getElementById('loginOverlay').classList.remove('hidden');
        }

        async function initApp() {
            await loadBranches();
            refreshCurrentModule();
        }

        async function loadBranches() {
            const res = await fetch('/api/branches');
            branches = await res.json();
            const selector = document.getElementById('branchSelector');
            selector.innerHTML = '';
            branches.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b.id;
                opt.textContent = b.name;
                if (currentBranchId === b.id) opt.selected = true;
                selector.appendChild(opt);
            });
            if (!currentBranchId && branches.length > 0) {
                currentBranchId = branches[0].id;
                selector.value = currentBranchId;
            }
        }

        document.getElementById('branchSelector').addEventListener('change', (e) => {
            currentBranchId = parseInt(e.target.value);
            refreshCurrentModule();
        });

        function openAddBranchModal() {
            document.getElementById('branchModal').classList.remove('hidden');
        }
        function closeAddBranchModal() {
            document.getElementById('branchModal').classList.add('hidden');
            document.getElementById('newBranchName').value = '';
        }

        async function createNewBranch() {
            const name = document.getElementById('newBranchName').value.trim();
            if (!name) return;
            const res = await fetch('/api/branches', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            if (res.ok) {
                const newBranch = await res.json();
                closeAddBranchModal();
                await loadBranches();
                currentBranchId = newBranch.id;
                document.getElementById('branchSelector').value = currentBranchId;
                refreshCurrentModule();
            } else {
                alert('Branch already exists or invalid name.');
            }
        }

        function switchModule(moduleName) {
            currentModule = moduleName;
            document.querySelectorAll('.sidebar-item').forEach(btn => btn.classList.remove('active'));
            event.currentTarget.classList.add('active');
            refreshCurrentModule();
        }

        async function refreshCurrentModule() {
            const container = document.getElementById('mainContent');
            if (currentModule === 'home') {
                renderHomeModule(container);
            } else if (currentModule === 'timetables') {
                renderTimetableModule(container);
            } else {
                await renderDataModule(container, currentModule);
            }
        }

        function renderHomeModule(container) {
            container.innerHTML = `
                <div class="space-y-8">
                    <div class="glass-panel border gold-border p-10 rounded-3xl relative overflow-hidden shadow-2xl">
                        <div class="max-w-3xl relative z-10 space-y-4">
                            <span class="text-xs uppercase tracking-widest px-3 py-1 rounded-full bg-[#1c1c1c] gold-gradient-text border gold-border font-extrabold">Executive Command Center</span>
                            <h2 class="text-4xl font-black text-white tracking-tight leading-tight">Institutional Operations, <span class="gold-gradient-text">Mastered.</span></h2>
                            <p class="text-lg text-gray-300 font-medium leading-relaxed pt-2">
                                We simplify the boring clerical work. Not by hiring more clerks, but by never needing to do so.
                            </p>
                            <div class="pt-4 flex items-center space-x-4">
                                <button onclick="switchModule('students')" class="gold-bg hover:opacity-95 text-black font-extrabold px-6 py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Manage Students</button>
                                <button onclick="switchModule('fees')" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border font-extrabold px-6 py-3 rounded-xl text-xs uppercase tracking-wider fast-transition">View Fees (INR ₹)</button>
                            </div>
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
                        <div class="glass-panel p-6 rounded-2xl border gold-border">
                            <div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Active Students</div>
                            <div class="text-3xl font-black gold-gradient-text" id="statStudents">—</div>
                        </div>
                        <div class="glass-panel p-6 rounded-2xl border gold-border">
                            <div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Faculty Members</div>
                            <div class="text-3xl font-black gold-gradient-text" id="statTeachers">—</div>
                        </div>
                        <div class="glass-panel p-6 rounded-2xl border gold-border">
                            <div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Classrooms Available</div>
                            <div class="text-3xl font-black gold-gradient-text" id="statClassrooms">—</div>
                        </div>
                        <div class="glass-panel p-6 rounded-2xl border gold-border">
                            <div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Fee Collection (INR)</div>
                            <div class="text-3xl font-black gold-gradient-text" id="statFees">₹0</div>
                        </div>
                    </div>
                </div>
            `;
            loadHomeStats();
        }

        async function loadHomeStats() {
            try {
                if(!currentBranchId) return;
                const sRes = await fetch(`/api/records/students/${currentBranchId}`);
                const students = await sRes.json();
                document.getElementById('statStudents').textContent = students.length;

                const tRes = await fetch(`/api/records/teachers/${currentBranchId}`);
                const teachers = await tRes.json();
                document.getElementById('statTeachers').textContent = teachers.length;

                const cRes = await fetch(`/api/records/classrooms/${currentBranchId}`);
                const rooms = await cRes.json();
                document.getElementById('statClassrooms').textContent = rooms.length;

                const fRes = await fetch(`/api/records/fees/${currentBranchId}`);
                const fees = await fRes.json();
                const total = fees.reduce((acc, curr) => acc + (curr.amount_inr || 0), 0);
                document.getElementById('statFees').textContent = `₹${total.toLocaleString('en-IN')}`;
            } catch(e) { console.error(e); }
        }

        async function renderDataModule(container, moduleName) {
            container.innerHTML = `
                <div class="space-y-6">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">${moduleName} Department</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Branch Synchronized • Document Supported</p>
                        </div>
                        <button onclick="openRecordModal('${moduleName}')" class="gold-bg hover:opacity-95 text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg flex items-center space-x-2">
                            <span>+ Add New Record</span>
                        </button>
                    </div>
                    <div class="glass-panel border gold-border rounded-2xl p-6 overflow-x-auto shadow-2xl">
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead id="moduleTableHead" class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border"></thead>
                            <tbody id="moduleTableBody"></tbody>
                        </table>
                    </div>
                </div>
            `;
            await loadModuleRecords(moduleName);
        }

        async function loadModuleRecords(moduleName) {
            if (!currentBranchId) return;
            const res = await fetch(`/api/records/${moduleName}/${currentBranchId}`);
            const records = await res.json();
            const thead = document.getElementById('moduleTableHead');
            const tbody = document.getElementById('moduleTableBody');
            
            if (records.length === 0) {
                thead.innerHTML = `<tr><th class="p-4">Status</th></tr>`;
                tbody.innerHTML = `<tr><td class="p-8 text-center text-gray-500">No records found for ${moduleName}. Click '+ Add New Record' to create one with document attachments.</td></tr>`;
                return;
            }

            const keys = Object.keys(records[0]).filter(k => k !== 'id' && k !== 'branch_id');
            thead.innerHTML = `<tr>${keys.map(k => `<th class="p-4 uppercase tracking-wider text-xs font-bold">${k.replace('_', ' ')}</th>`).join('')}</tr>`;
            
            tbody.innerHTML = records.map(r => `
                <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                    ${keys.map(k => {
                        let val = r[k];
                        if (moduleName === 'fees' && k === 'amount_inr') {
                            val = `₹${parseFloat(val || 0).toLocaleString('en-IN')}`;
                        }
                        if (k === 'document' && val) {
                            val = `<a href="/uploads/${val}" target="_blank" class="text-yellow-500 underline text-xs font-semibold">View File</a>`;
                        } else if (k === 'document' && !val) {
                            val = `<span class="text-gray-600 text-xs">No File</span>`;
                        }
                        return `<td class="p-4 font-medium">${val}</td>`;
                    }).join('')}
                </tr>
            `).join('');
        }

        async function renderTimetableModule(container) {
            // Fetch teachers list for configuration
            const tRes = await fetch(`/api/records/teachers/${currentBranchId}`);
            const teachers = await tRes.json();
            
            const sRes = await fetch(`/api/timetable/slots/${currentBranchId}`);
            const savedSlots = await sRes.json();

            container.innerHTML = `
                <div class="space-y-8">
                    <div class="flex justify-between items-center">
                        <div>
                            <h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">Timetable Generation & Batch Scheduler</h2>
                            <p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">Constraint-based automated scheduler with PDF Export</p>
                        </div>
                        <button onclick="window.print()" class="bg-[#141414] hover:bg-[#202020] gold-gradient-text border gold-border px-5 py-2.5 rounded-xl text-xs font-extrabold uppercase tracking-wider fast-transition shadow-lg">Download PDF / Print Timetable</button>
                    </div>

                    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                        <div class="glass-panel border gold-border p-6 rounded-2xl space-y-6">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Configure Batch & Teacher Load</h3>
                            <div class="space-y-4">
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Batch Name</label>
                                    <input type="text" id="ttBatchName" placeholder="e.g. B.Tech CSE Batch A" value="B.Tech CSE Batch A" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                                </div>
                                <div>
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Lecture Timings (Comma separated)</label>
                                    <input type="text" id="ttTimings" value="09:00 AM - 10:00 AM, 10:00 AM - 11:00 AM, 11:15 AM - 12:15 PM, 01:15 PM - 02:15 PM" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-xs text-gray-200 gold-border-glow focus:outline-none">
                                </div>
                                <div class="pt-2">
                                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Assigned Teachers & Constraints</label>
                                    <div id="teacherConfigList" class="space-y-3 max-h-60 overflow-y-auto pr-2">
                                        ${teachers.length === 0 ? '<p class="text-xs text-gray-500">No teachers found in teacher database. Please add teachers first.</p>' : 
                                          teachers.map((t, idx) => `
                                            <div class="p-3 bg-[#0f0f0f] border gold-border rounded-xl space-y-2" data-teacher="${t.name}" data-subject="${t.subject}">
                                                <div class="flex justify-between items-center text-xs font-bold text-gray-200">
                                                    <span>${t.name} (${t.subject})</span>
                                                </div>
                                                <div class="grid grid-cols-2 gap-2">
                                                    <div>
                                                        <label class="text-[10px] text-gray-400 uppercase">Lectures/Week</label>
                                                        <input type="number" id="lec_${idx}" value="3" min="1" max="5" class="w-full bg-[#070707] border gold-border rounded p-1.5 text-xs text-white">
                                                    </div>
                                                    <div>
                                                        <label class="text-[10px] text-gray-400 uppercase">Unavailable Days</label>
                                                        <input type="text" id="unav_${idx}" placeholder="e.g. Monday" class="w-full bg-[#070707] border gold-border rounded p-1.5 text-xs text-white" title="Comma separated days">
                                                    </div>
                                                </div>
                                            </div>
                                          `).join('')}
                                    </div>
                                </div>
                                <button onclick="generateTimetableSchedule()" class="w-full gold-bg hover:opacity-95 text-black font-extrabold py-3 rounded-xl text-xs uppercase tracking-wider fast-transition shadow-lg">Generate Weekly Timetable</button>
                            </div>
                        </div>

                        <div class="lg:col-span-2 glass-panel border gold-border p-6 rounded-2xl overflow-x-auto">
                            <h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider mb-4">Generated Weekly Schedule</h3>
                            <table class="w-full text-left text-sm text-gray-300">
                                <thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border">
                                    <tr>
                                        <th class="p-3">Day</th>
                                        <th class="p-3">Time Slot</th>
                                        <th class="p-3">Subject</th>
                                        <th class="p-3">Teacher</th>
                                        <th class="p-3">Room</th>
                                    </tr>
                                </thead>
                                <tbody id="timetableSlotsBody">
                                    ${savedSlots.length === 0 ? '<tr><td colspan="5" class="p-6 text-center text-gray-500">No timetable generated yet. Configure and click generate.</td></tr>' : 
                                      savedSlots.map(s => `
                                        <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
                                            <td class="p-3 font-semibold text-yellow-500">${s.day}</td>
                                            <td class="p-3">${s.time_slot}</td>
                                            <td class="p-3 font-medium">${s.subject}</td>
                                            <td class="p-3">${s.teacher}</td>
                                            <td class="p-3 text-xs text-gray-400">${s.room}</td>
                                        </tr>
                                      `).join('')}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
        }

        async function generateTimetableSchedule() {
            const batchName = document.getElementById('ttBatchName').value;
            const timingsRaw = document.getElementById('ttTimings').value;
            const timings = timingsRaw.split(',').map(s => s.trim()).filter(Boolean);
            
            const teacherElements = document.querySelectorAll('#teacherConfigList > div');
            const teachers_config = [];
            
            teacherElements.forEach((el, idx) => {
                const name = el.getAttribute('data-teacher');
                const subject = el.getAttribute('data-subject');
                const lectures_per_week = document.getElementById(`lec_${idx}`).value;
                const unavRaw = document.getElementById(`unav_${idx}`).value;
                const unavailable_days = unavRaw.split(',').map(s => s.trim()).filter(Boolean);
                
                teachers_config.push({ name, subject, lectures_per_week, unavailable_days });
            });

            const res = await fetch('/api/timetable/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ branch_id: currentBranchId, batch_name: batchName, teachers_config, timings })
            });

            if (res.ok) {
                alert('Timetable successfully generated and locked in database!');
                refreshCurrentModule();
            } else {
                alert('Failed to generate timetable.');
            }
        }

        function openRecordModal(moduleName) {
            document.getElementById('recordModal').classList.remove('hidden');
            document.getElementById('modalTitle').textContent = `Add New ${moduleName} Record`;
            const fieldsContainer = document.getElementById('modalFields');
            document.getElementById('recordFile').value = '';
            
            let fieldsConfig = [];
            if (moduleName === 'students') {
                fieldsConfig = [
                    { id: 'name', label: 'Full Name', type: 'text', placeholder: 'Aarav Sharma' },
                    { id: 'email', label: 'Email Address', type: 'email', placeholder: 'aarav@institution.edu' },
                    { id: 'course', label: 'Course / Program', type: 'text', placeholder: 'B.Tech Computer Science' },
                    { id: 'status', label: 'Status', type: 'text', placeholder: 'Active' }
                ];
            } else if (moduleName === 'teachers') {
                fieldsConfig = [
                    { id: 'name', label: 'Teacher Name', type: 'text', placeholder: 'Dr. Ramesh Kumar' },
                    { id: 'subject', label: 'Specialization', type: 'text', placeholder: 'Artificial Intelligence' },
                    { id: 'department', label: 'Department', type: 'text', placeholder: 'School of Engineering' }
                ];
            } else if (moduleName === 'classrooms') {
                fieldsConfig = [
                    { id: 'room_no', label: 'Room Number', type: 'text', placeholder: 'Lecture Hall 402' },
                    { id: 'capacity', label: 'Seating Capacity', type: 'number', placeholder: '120' },
                    { id: 'building', label: 'Building Name', type: 'text', placeholder: 'Apex Tower' }
                ];
            } else if (moduleName === 'syllabus') {
                fieldsConfig = [
                    { id: 'subject', label: 'Subject Name', type: 'text', placeholder: 'Data Structures & Algorithms' },
                    { id: 'semester', label: 'Semester', type: 'text', placeholder: 'Fall 2026' },
                    { id: 'units', label: 'Credit Units', type: 'number', placeholder: '4' }
                ];
            } else if (moduleName === 'attendance') {
                fieldsConfig = [
                    { id: 'student_name', label: 'Student Name', type: 'text', placeholder: 'Priya Patel' },
                    { id: 'date', label: 'Date', type: 'text', placeholder: '2026-09-01' },
                    { id: 'status', label: 'Attendance Status', type: 'text', placeholder: 'Present' }
                ];
            } else if (moduleName === 'invigilation') {
                fieldsConfig = [
                    { id: 'teacher_name', label: 'Faculty Name', type: 'text', placeholder: 'Prof. Vikram Joshi' },
                    { id: 'exam_date', label: 'Exam Date', type: 'text', placeholder: '2026-09-15' },
                    { id: 'room', label: 'Exam Hall', type: 'text', placeholder: 'Examination Block B' }
                ];
            } else if (moduleName === 'fees') {
                fieldsConfig = [
                    { id: 'student_name', label: 'Student Name', type: 'text', placeholder: 'Rohan Sharma' },
                    { id: 'amount_inr', label: 'Fee Amount (INR ₹)', type: 'number', placeholder: '75000' },
                    { id: 'status', label: 'Payment Status', type: 'text', placeholder: 'Paid / Pending' },
                    { id: 'due_date', label: 'Due Date', type: 'text', placeholder: '2026-09-30' }
                ];
            }

            fieldsContainer.innerHTML = fieldsConfig.map(f => `
                <div>
                    <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">${f.label}</label>
                    <input type="${f.type}" id="field_${f.id}" required placeholder="${f.placeholder}" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none">
                </div>
            `).join('');
            
            window.activeModalModule = moduleName;
        }

        function closeRecordModal() {
            document.getElementById('recordModal').classList.add('hidden');
        }

        async function submitRecordForm(e) {
            e.preventDefault();
            const moduleName = window.activeModalModule;
            const inputs = document.getElementById('modalFields').querySelectorAll('input');
            const data = {};
            inputs.forEach(input => {
                const key = input.id.replace('field_', '');
                data[key] = input.type === 'number' ? parseFloat(input.value) : input.value;
            });

            const formData = new FormData();
            formData.append('branch_id', currentBranchId);
            formData.append('data_json', JSON.stringify(data));
            
            const fileInput = document.getElementById('recordFile');
            if (fileInput.files[0]) {
                formData.append('file', fileInput.files[0]);
            }

            const res = await fetch(`/api/records/${moduleName}`, {
                method: 'POST',
                body: formData
            });

            if (res.ok) {
                closeRecordModal();
                await loadModuleRecords(moduleName);
            } else {
                alert('Failed to save record with document.');
            }
        }
    </script>
</body>
</html>
"""
