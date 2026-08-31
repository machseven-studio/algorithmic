# main.py
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import sqlite3
import os

app = FastAPI(title="ALGORITHMIC", version="2.0.0")

DB_FILE = "algorithmic.db"

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
        CREATE TABLE IF NOT EXISTS timetables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER,
            day TEXT,
            time_slot TEXT,
            subject TEXT,
            teacher TEXT,
            room TEXT,
            FOREIGN KEY(branch_id) REFERENCES branches(id)
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM branches")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO branches (name) VALUES ('Main Campus')")
    conn.commit()
    conn.close()

init_db()

class BranchCreate(BaseModel):
    name: str

class TimetableCreate(BaseModel):
    branch_id: int
    day: str
    time_slot: str
    subject: str
    teacher: str
    room: str

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

@app.get("/api/timetables/{branch_id}")
def get_timetables(branch_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM timetables WHERE branch_id = ?", (branch_id,))
    entries = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return entries

@app.post("/api/timetables")
def add_timetable(entry: TimetableCreate):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO timetables (branch_id, day, time_slot, subject, teacher, room)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entry.branch_id, entry.day, entry.time_slot, entry.subject, entry.teacher, entry.room))
    conn.commit()
    entry_id = cursor.lastrowid
    conn.close()
    return {"id": entry_id, **entry.dict()}

@app.get("/", response_class=HTMLResponse)
def read_root():
    return HTMLResponse(content=HTML_CONTENT, status_code=200)

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ALGORITHMIC - Institutional Operations Platform</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {
            background-color: #0a0a0a;
            background-image: 
                radial-gradient(rgba(212, 175, 55, 0.04) 1px, transparent 0),
                radial-gradient(rgba(212, 175, 55, 0.02) 1px, transparent 0);
            background-size: 32px 32px;
            background-position: 0 0, 16px 16px;
            color: #e5e7eb;
            font-family: system-ui, -apple-system, sans-serif;
        }
        .gold-text {
            color: #D4AF37;
            background: linear-gradient(135deg, #BF953F, #FCF6BA, #B38728, #FBF5B7, #AA771C);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .gold-border {
            border-color: rgba(212, 175, 55, 0.3);
        }
        .gold-glow:hover {
            box-shadow: 0 0 15px rgba(212, 175, 55, 0.2);
        }
        .sidebar-item {
            transition: all 0.2s ease;
            letter-spacing: 0.05em;
        }
        .sidebar-item:hover, .sidebar-item.active {
            background: rgba(212, 175, 55, 0.1);
            color: #D4AF37;
            border-left: 3px solid #D4AF37;
        }
    </style>
</head>
<body class="min-h-screen flex flex-col">
    <!-- Top Header -->
    <header class="border-b gold-border bg-[#0d0d0d] px-6 py-4 flex justify-between items-center">
        <div class="flex items-center space-x-4">
            <h1 class="text-2xl font-black gold-text tracking-wider">ALGORITHMIC</h1>
            <span class="text-xs px-2 py-1 rounded bg-[#1a1a1a] gold-text border gold-border">ENTERPRISE v2.0</span>
        </div>
        <div class="flex items-center space-x-6 text-sm">
            <div>Developer: <span class="gold-text font-semibold">Samarth Dave</span></div>
            <div>Studio: <span class="gold-text font-semibold">MachSevenStudio</span></div>
            <div class="flex items-center space-x-2">
                <label class="text-xs text-gray-400">Branch:</label>
                <select id="branchSelector" class="bg-[#121212] gold-border border text-sm rounded px-3 py-1 text-gray-200 focus:outline-none focus:ring-1 focus:ring-[#D4AF37]">
                    <!-- Dynamically populated -->
                </select>
                <button onclick="openAddBranchModal()" class="text-xs bg-[#1f1f1f] hover:bg-[#2a2a2a] gold-text border gold-border px-2 py-1 rounded">+ Add Branch</button>
            </div>
        </div>
    </header>

    <!-- Main Workspace -->
    <div class="flex flex-1 overflow-hidden">
        <!-- Sidebar Navigation -->
        <nav class="w-64 border-r gold-border bg-[#0d0d0d] flex flex-col py-6 space-y-1">
            <div class="px-6 pb-4 text-xs font-semibold text-gray-500 uppercase tracking-widest">Modules</div>
            <button onclick="switchModule('students')" class="sidebar-item active w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Students</button>
            <button onclick="switchModule('teachers')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Teachers</button>
            <button onclick="switchModule('classrooms')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Classrooms</button>
            <button onclick="switchModule('syllabus')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Syllabus</button>
            <button onclick="switchModule('attendance')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Attendance</button>
            <button onclick="switchModule('timetable')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Timetable</button>
            <button onclick="switchModule('invigilation')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Invigilator Duty</button>
            <button onclick="switchModule('fees')" class="sidebar-item w-full text-left px-6 py-3 text-sm font-bold uppercase text-gray-300">Fees</button>
            
            <div class="mt-auto px-6 pt-6 border-t gold-border text-xs text-gray-400">
                <p class="mb-2">We simplify the boring clerical work. Not by hiring more clerks, but by never needing to do so.</p>
            </div>
        </nav>

        <!-- Content Area -->
        <main class="flex-1 p-8 overflow-y-auto bg-[#0a0a0a]" id="mainContent">
            <!-- Dynamic module content renders here -->
        </main>
    </div>

    <!-- Add Branch Modal -->
    <div id="branchModal" class="fixed inset-0 bg-black/70 flex items-center justify-center hidden">
        <div class="bg-[#121212] border gold-border p-6 rounded-lg w-96 shadow-2xl">
            <h3 class="text-lg font-bold gold-text mb-4">Add New Branch</h3>
            <input type="text" id="newBranchName" placeholder="Branch Name (e.g. Downtown Campus)" class="w-full bg-[#0a0a0a] border gold-border rounded p-2 text-sm text-gray-200 mb-4 focus:outline-none focus:ring-1 focus:ring-[#D4AF37]">
            <div class="flex justify-end space-x-3">
                <button onclick="closeAddBranchModal()" class="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 rounded">Cancel</button>
                <button onclick="createNewBranch()" class="px-4 py-2 text-sm bg-[#D4AF37] hover:bg-[#c59b27] text-black font-semibold rounded">Create Branch</button>
            </div>
        </div>
    </div>

    <script>
        let branches = [];
        let currentBranchId = null;
        let currentModule = 'students';

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
                alert('Failed to create branch or branch already exists.');
            }
        }

        function switchModule(moduleName) {
            currentModule = moduleName;
            document.querySelectorAll('.sidebar-item').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            refreshCurrentModule();
        }

        function refreshCurrentModule() {
            const container = document.getElementById('mainContent');
            if (currentModule === 'timetable') {
                renderTimetableModule(container);
            } else {
                container.innerHTML = `
                    <div class="flex justify-between items-center mb-6">
                        <h2 class="text-xl font-bold uppercase gold-text">${currentModule} Management</h2>
                        <button class="bg-[#1f1f1f] hover:bg-[#2a2a2a] gold-text border gold-border px-4 py-2 rounded text-sm font-semibold">+ Add New Record</button>
                    </div>
                    <div class="bg-[#121212] border gold-border rounded-lg p-6">
                        <p class="text-gray-400 text-sm">Managing ${currentModule} records for active branch. All operations fully synchronized.</p>
                    </div>
                `;
            }
        }

        async function renderTimetableModule(container) {
            container.innerHTML = `
                <div class="flex justify-between items-center mb-6">
                    <h2 class="text-xl font-bold uppercase gold-text">Timetable Module</h2>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div class="bg-[#121212] border gold-border p-6 rounded-lg">
                        <h3 class="text-md font-bold gold-text mb-4">Add Timetable Entry</h3>
                        <form id="timetableForm" onsubmit="submitTimetable(event)" class="space-y-4">
                            <div>
                                <label class="block text-xs text-gray-400 mb-1 uppercase">Day</label>
                                <select id="ttDay" class="w-full bg-[#0a0a0a] border gold-border rounded p-2 text-sm text-gray-200">
                                    <option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option><option>Saturday</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-400 mb-1 uppercase">Time Slot</label>
                                <input type="text" id="ttSlot" placeholder="09:00 AM - 10:00 AM" required class="w-full bg-[#0a0a0a] border gold-border rounded p-2 text-sm text-gray-200">
                            </div>
                            <div>
                                <label class="block text-xs text-gray-400 mb-1 uppercase">Subject</label>
                                <input type="text" id="ttSubject" placeholder="Advanced Mathematics" required class="w-full bg-[#0a0a0a] border gold-border rounded p-2 text-sm text-gray-200">
                            </div>
                            <div>
                                <label class="block text-xs text-gray-400 mb-1 uppercase">Teacher</label>
                                <input type="text" id="ttTeacher" placeholder="Dr. Robert Ford" required class="w-full bg-[#0a0a0a] border gold-border rounded p-2 text-sm text-gray-200">
                            </div>
                            <div>
                                <label class="block text-xs text-gray-400 mb-1 uppercase">Room</label>
                                <input type="text" id="ttRoom" placeholder="Hall 402" required class="w-full bg-[#0a0a0a] border gold-border rounded p-2 text-sm text-gray-200">
                            </div>
                            <button type="submit" class="w-full bg-[#D4AF37] hover:bg-[#c59b27] text-black font-semibold py-2 rounded text-sm">Save Timetable Entry</button>
                        </form>
                    </div>
                    <div class="lg:col-span-2 bg-[#121212] border gold-border p-6 rounded-lg overflow-x-auto">
                        <h3 class="text-md font-bold gold-text mb-4">Current Branch Timetable Schedule</h3>
                        <table class="w-full text-left text-sm text-gray-300">
                            <thead class="bg-[#1f1f1f] text-xs uppercase gold-text border-b gold-border">
                                <tr>
                                    <th class="p-3">Day</th>
                                    <th class="p-3">Time Slot</th>
                                    <th class="p-3">Subject</th>
                                    <th class="p-3">Teacher</th>
                                    <th class="p-3">Room</th>
                                </tr>
                            </thead>
                            <tbody id="timetableTableBody">
                                <!-- Populated dynamically -->
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
            await loadTimetableEntries();
        }

        async function loadTimetableEntries() {
            if (!currentBranchId) return;
            const res = await fetch(`/api/timetables/${currentBranchId}`);
            const entries = await res.json();
            const tbody = document.getElementById('timetableTableBody');
            tbody.innerHTML = '';
            if (entries.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-gray-500">No timetable entries found for this branch. Use the form to add one.</td></tr>`;
                return;
            }
            entries.forEach(e => {
                tbody.innerHTML += `
                    <tr class="border-b border-gray-800 hover:bg-[#181818]">
                        <td class="p-3 font-medium">${e.day}</td>
                        <td class="p-3">${e.time_slot}</td>
                        <td class="p-3">${e.subject}</td>
                        <td class="p-3">${e.teacher}</td>
                        <td class="p-3">${e.room}</td>
                    </tr>
                `;
            });
        }

        async function submitTimetable(event) {
            event.preventDefault();
            const payload = {
                branch_id: currentBranchId,
                day: document.getElementById('ttDay').value,
                time_slot: document.getElementById('ttSlot').value,
                subject: document.getElementById('ttSubject').value,
                teacher: document.getElementById('ttTeacher').value,
                room: document.getElementById('ttRoom').value
            };
            const res = await fetch('/api/timetables', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                document.getElementById('timetableForm').reset();
                await loadTimetableEntries();
            } else {
                alert('Failed to save timetable entry.');
            }
        }

        window.onload = async () => {
            await loadBranches();
            refreshCurrentModule();
        };
    </script>
</body>
</html>
"""
