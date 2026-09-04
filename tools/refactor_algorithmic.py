from pathlib import Path
import re

# main.py
p = Path('main.py')
s = p.read_text(encoding='utf-8')

if 'import random\n' not in s:
    s = s.replace('import os\n', 'import os\nimport random\n', 1)

# Remove the obsolete external fixes route.
s = re.sub(
    r'\n@app\.get\("/algorithmic_fixes\.js"\)\ndef algorithmic_fixes\(\):\n'
    r'    return FileResponse\(Path\(__file__\)\.with_name\("algorithmic_fixes\.js"\), media_type="application/javascript"\)\n?',
    '\n', s,
)

# Replace the old batch-adjacency solver with a simple grid mapper. The caller
# randomizes the pool and controls which students are consumed by each room.
seating_fn = '''def _build_seating_layout(students, rows, columns):
    """Map an already-randomized student pool onto a rows x columns grid."""
    capacity = rows * columns
    selected = students[:capacity]
    return [
        {
            "row": (index // columns) + 1,
            "column": (index % columns) + 1,
            "student_id": student["id"],
            "name": student["name"],
            "batch": student["batch"],
            "roll_number": student["roll_number"],
        }
        for index, student in enumerate(selected)
    ]


'''
s, n = re.subn(
    r'def _build_seating_layout\(students, rows, columns\):.*?(?=\n@app\.get\("/api/seating/\{branch_id\}"\))',
    seating_fn, s, flags=re.S,
)
if n != 1:
    raise SystemExit(f'Expected one seating layout function, found {n}')

# Replace room/student selection in seating generation. Classrooms are physical
# resources for the whole institute, while students remain branch-scoped.
room_student_re = re.compile(
    r'    conn = get_conn\(\)\n'
    r'    room_cur = conn\.cursor\(\); room_cur\.execute\("SELECT room_no, capacity FROM classrooms WHERE branch_id = %s AND room_no = %s", \(req\.branch_id, room_number\)\); room = room_cur\.fetchone\(\).*?'
    r'    assignments = _build_seating_layout\(student_rows, req\.rows, req\.columns\)\n',
    flags=re.S,
)
room_student_block = '''    conn = get_conn()

    # Any registered classroom in this institute may host an exam. Classroom
    # department/branch ownership is intentionally not a seating restriction.
    room_cur = conn.cursor()
    room_cur.execute(
        """SELECT c.room_no, c.capacity
           FROM classrooms c
           JOIN branches b ON b.id = c.branch_id
           WHERE b.tenant_id = %s AND c.room_no = %s
           LIMIT 1""",
        (institute.id, room_number),
    )
    room = room_cur.fetchone()
    if not room:
        conn.close()
        raise HTTPException(status_code=400, detail="Selected exam room is not registered for this institute.")

    requested_capacity = req.rows * req.columns
    room_capacity = int(room[1] or 0)
    if room_capacity <= 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Selected room has no valid seating capacity.")
    if requested_capacity > room_capacity:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Grid capacity ({requested_capacity}) exceeds room capacity ({room_capacity}).")

    # Re-generating one room replaces only that room's previous allocation.
    cursor = conn.cursor()
    cursor.execute(
        "SELECT assignments_json FROM exam_seatings WHERE branch_id = %s AND exam_date = %s AND room_number = %s",
        (req.branch_id, req.exam_date, room_number),
    )
    old_room = cursor.fetchone()
    old_student_ids = set()
    if old_room:
        try:
            old_student_ids = {
                int(a["student_id"])
                for a in json.loads(old_room["assignments_json"] or "[]")
                if a.get("student_id") is not None
            }
        except (TypeError, ValueError, KeyError):
            pass
    cursor.execute(
        "DELETE FROM exam_seatings WHERE branch_id = %s AND exam_date = %s AND room_number = %s",
        (req.branch_id, req.exam_date, room_number),
    )

    # Fetch the selected branch's complete student pool, remove students already
    # consumed by other rooms for this exam, then randomize the remaining pool.
    cursor.execute(
        """SELECT id, name, COALESCE(batch, '') AS batch, COALESCE(roll_number, '') AS roll_number
           FROM students WHERE branch_id = %s""",
        (req.branch_id,),
    )
    student_rows = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        "SELECT assignments_json FROM exam_seatings WHERE branch_id = %s AND exam_date = %s",
        (req.branch_id, req.exam_date),
    )
    assigned_elsewhere = set()
    for row in cursor.fetchall():
        try:
            assigned_elsewhere.update(
                int(a["student_id"])
                for a in json.loads(row["assignments_json"] or "[]")
                if a.get("student_id") is not None
            )
        except (TypeError, ValueError, KeyError):
            continue
    assigned_elsewhere.difference_update(old_student_ids)

    remaining_students = [
        student for student in student_rows
        if int(student["id"]) not in assigned_elsewhere
    ]
    random.shuffle(remaining_students)
    if len(remaining_students) < requested_capacity:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"Only {len(remaining_students)} unassigned students remain for this exam; {requested_capacity} seats were requested.",
        )
    assignments = _build_seating_layout(remaining_students, req.rows, req.columns)
'''
s, n = room_student_re.subn(room_student_block, s, count=1)
if n != 1:
    raise SystemExit(f'Expected one seating room/student block, found {n}')

# The original INSERT section now follows the replacement. Its duplicate DELETE
# is already performed above, so remove that exact second delete only.
duplicate_delete = '''    cursor = conn.cursor()
    cursor.execute(
        """DELETE FROM exam_seatings WHERE branch_id = %s AND exam_date = %s AND room_number = %s""",
        (req.branch_id, req.exam_date, room_number),
    )
    cursor.execute(
        """INSERT INTO exam_seatings'''
insert_only = '''    cursor.execute(
        """INSERT INTO exam_seatings'''
if duplicate_delete in s:
    s = s.replace(duplicate_delete, insert_only, 1)

# Attendance history is deliberately serialized as primitive strings.
history_block_re = re.compile(
    r'(@app\.get\("/api/attendance/history/\{branch_id\}"\).*?)(?=\n# -{3,}\n)',
    flags=re.S,
)
m = history_block_re.search(s)
if m:
    block = m.group(1)
    block = re.sub(
        r'    history = \[dict\(r\) for r in cursor\.fetchall\(\)\]',
        '    history = [{"date": str(r["date"] or ""), "status": str(r["status"] or "")} for r in cursor.fetchall()]',
        block,
    )
    s = s[:m.start(1)] + block + s[m.end(1):]

p.write_text(s, encoding='utf-8')

# index.html
p = Path('index.html')
s = p.read_text(encoding='utf-8')

# All application frontend code lives in this file; remove dead repair-layer tags.
s = re.sub(
    r'\s*<script src="/(?:algorithmic_fixes|algorithmic_chart_boot|algorithmic_final|final_frontend_repairs|algorithmic_nav_repair)\.js"></script>',
    '', s,
)
s = re.sub(
    r'<script src="https://cdnjs\.cloudflare\.com/ajax/libs/Chart\.js/4\.4\.4/chart\.umd\.min\.js"></script>',
    '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>', s, count=1,
)

# Chart.js must exist before any chart is instantiated.
chart_guard = """            if (!window.Chart) {
                const error = document.getElementById('centralAnalyticsError');
                if (error) error.textContent = 'Analytics charts are unavailable because Chart.js did not load.';
                return;
            }

"""
chart_marker = "            const gridColor = 'rgba(212,175,55,0.12)';"
if chart_marker in s and chart_guard not in s:
    s = s.replace(chart_marker, chart_guard + chart_marker, 1)

for ctx in ('attendanceCtx', 'batchCtx', 'revenueCtx'):
    s = s.replace(
        f'if ({ctx}) {{\n                window.__centralAnalyticsCharts.push(new Chart({ctx}, {{',
        f'if ({ctx} && window.Chart) {{\n                window.__centralAnalyticsCharts.push(new Chart({ctx}, {{',
        1,
    )

# Attendance UI consumes primitives only.
s = s.replace(
    "const date = typeof h.date === 'object' ? JSON.stringify(h.date) : String(h.date ?? '');",
    "const date = String(h?.date ?? '');",
)
s = s.replace(
    "const status = typeof h.status === 'object' ? (h.status?.status || h.status?.value || JSON.stringify(h.status)) : String(h.status ?? '');",
    "const status = String(h?.status ?? '');",
)

# Timetable teacher workspace.
timetable_css = '''
<style id="algorithmic-timetable-layout">
#teacherConfigList{min-height:220px;max-height:55vh!important;overflow-y:auto;padding-right:.5rem}
#teacherConfigList>div{min-height:84px}
#teacherConfigList>div>div:last-child{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:.75rem}
#ttTimingRows{max-height:55vh;overflow-y:auto;padding-right:.5rem}
.glass-panel:has(#teacherConfigList){width:91.666667%!important;max-width:64rem!important;max-height:90vh;overflow:hidden}
@media(max-width:768px){#teacherConfigList>div>div:last-child{grid-template-columns:1fr}.glass-panel:has(#teacherConfigList){width:95%!important}}
</style>
'''
if 'id="algorithmic-timetable-layout"' not in s:
    s = s.replace('</head>', timetable_css + '\n</head>', 1)

# Sidebar alignment, collapse control, and arrow-only child toggles.
sidebar_css = '''
<style id="algorithmic-sidebar-layout">
#moduleSidebar{display:flex;flex-direction:column;align-items:stretch;position:relative!important;transition:width .18s ease,min-width .18s ease,padding .18s ease!important}
#moduleGroups{display:flex;flex-direction:column;align-items:stretch;gap:2px;width:100%}
#moduleGroups>.mb-1,#moduleGroups .module-head-group{width:100%}
#moduleGroups .sidebar-item{width:100%;min-height:42px;display:flex;align-items:center}
#moduleSidebar.sidebar-collapsed{width:72px!important;min-width:72px!important}
#moduleSidebar.sidebar-collapsed .sidebar-label,#moduleSidebar.sidebar-collapsed .module-label,#moduleSidebar.sidebar-collapsed .module-head-title,#moduleSidebar.sidebar-collapsed .module-head-text{display:none!important}
#moduleSidebar.sidebar-collapsed .sidebar-item{justify-content:center;padding-left:0!important;padding-right:0!important;border-left-width:0!important}
#sidebarCollapseButton{position:absolute;top:10px;right:-14px;z-index:30;width:28px;height:28px;border:1px solid rgba(212,175,55,.38);border-radius:999px;background:#0b0b0b;color:#e8c767;font-size:18px;line-height:24px;box-shadow:0 4px 12px rgba(0,0,0,.45);cursor:pointer}
#sidebarCollapseButton:hover{border-color:#d4af37}
.module-head-arrow{flex:0 0 auto;cursor:pointer}
.module-head-title{cursor:pointer}
</style>
'''
if 'id="algorithmic-sidebar-layout"' not in s:
    s = s.replace('</head>', sidebar_css + '\n</head>', 1)

# Add the collapse button to the real sidebar.
if 'id="sidebarCollapseButton"' not in s:
    needle = '    <div class="px-6 pb-1 elegant-font text-lg font-bold gold-gradient-text tracking-wide">ALGORITHMIC</div>'
    button = '    <button id="sidebarCollapseButton" type="button" aria-label="Collapse sidebar" title="Collapse sidebar" onclick="toggleSidebar()">‹</button>\n'
    if needle not in s:
        raise SystemExit('Sidebar insertion point not found')
    s = s.replace(needle, button + needle, 1)

# Replace the canonical head renderer so text navigates and only the arrow toggles.
head_re = re.compile(r'function renderModuleGroups\(\) \{.*?\n        \}\n\n        function ', flags=re.S)
m = head_re.search(s)
if not m:
    raise SystemExit('renderModuleGroups block not found')
head_replacement = '''function renderModuleGroups() {
            const groups = document.getElementById('moduleGroups');
            if (!groups) return;
            groups.innerHTML = ACCESS_HEADS.filter(h => isOwner || myAllowedModules.includes(h)).map(head => {
                const open = head === 'homepage' && isOwner;
                const children = HEAD_MODULES[head].filter(([m]) => m !== 'users' || isOwner);
                const title = head === 'homepage' ? 'Homepage' : head === 'administrations' ? 'Administrations' : 'Examination';
                return `<div class="mb-1 module-head-group" data-head="${head}">
                    <div class="w-full flex items-center justify-between px-3 py-3 rounded-lg text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-yellow-500 hover:bg-[#111]">
                        <span class="module-head-title module-head-text flex-1" onclick="switchHead('${head}')">${title}</span>
                        <button type="button" class="module-head-arrow head-chevron ${open ? 'rotate-180' : ''}" aria-label="Toggle ${title}" onclick="toggleModuleHead('${head}', event)">⌄</button>
                    </div>
                    <div id="head-${head}" class="${open ? '' : 'hidden'} space-y-0.5">` +
                    children.map(([mod, icon, label]) => `<button onclick="switchModule('${mod}', this)" data-module="${mod}" class="sidebar-item w-full text-left px-3 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3 rounded-lg"><span>${icon}</span><span class="module-label">${label}</span></button>`).join('') +
                    `</div>
                </div>`;
            }).join('');
        }

        function switchHead(head) {
            if (!isOwner && !myAllowedModules.includes(head)) return;
            currentModule = 'home';
            document.querySelectorAll('.sidebar-item').forEach(btn => btn.classList.remove('active'));
            refreshCurrentModule();
        }

        function toggleSidebar() {
            const sidebar = document.getElementById('moduleSidebar');
            const button = document.getElementById('sidebarCollapseButton');
            if (!sidebar) return;
            const collapsed = sidebar.classList.toggle('sidebar-collapsed');
            if (button) {
                button.textContent = collapsed ? '›' : '‹';
                button.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
                button.setAttribute('aria-label', button.title);
            }
        }

        function toggleModuleHead(head, event) {
            if (event) event.stopPropagation();
            const panel = document.getElementById(`head-${head}`);
            const button = panel?.previousElementSibling?.querySelector('.module-head-arrow');
            if (!panel) return;
            panel.classList.toggle('hidden');
            if (button) button.classList.toggle('rotate-180');
        }

        function '''
s = s[:m.start()] + head_replacement + s[m.end()-len('function '):]

# Examination Results/History cards use the same visual language as the main modules.
exam_css = '''
<style id="algorithmic-examination-layout">
#examV2Root .examv2-card{background:linear-gradient(160deg,rgba(20,17,10,.55),rgba(8,8,8,.9));border:1px solid rgba(212,175,55,.18);border-radius:16px;padding:24px;box-shadow:0 12px 32px rgba(0,0,0,.35)}
#examV2Root .examv2-table th{padding:12px 14px;color:#9ca3af;border-bottom:1px solid rgba(212,175,55,.18)}
#examV2Root .examv2-table td{padding:11px 14px;border-bottom:1px solid #181818}
#examV2Root .examv2-btn{border-radius:10px;padding:10px 14px}
#examV2Root .examv2-input{border-radius:10px;padding:11px 13px}
</style>
'''
if 'id="algorithmic-examination-layout"' not in s:
    s = s.replace('</head>', exam_css + '\n</head>', 1)

# Remove the obsolete zero-width collapse rules left by the previous repair layer.
s = s.replace('#moduleSidebar.sidebar-collapsed{width:0!important;min-width:0!important;padding:0!important;border-width:0!important}\n', '')
s = s.replace('#moduleSidebar.sidebar-collapsed>*:not(#sidebarCollapseButton):not(#algorithmicSidebarToggle){display:none!important}\n', '')
s = s.replace('#moduleSidebar.sidebar-collapsed #sidebarCollapseButton,#moduleSidebar.sidebar-collapsed #algorithmicSidebarToggle{display:block!important;visibility:visible!important;opacity:1!important;right:-30px!important}\n', '')

p.write_text(s, encoding='utf-8')
print('Algorithmic source refactor applied')
