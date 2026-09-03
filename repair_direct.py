from pathlib import Path
import re
import textwrap

main_path = Path('main.py')
index_path = Path('index.html')
main = main_path.read_text(encoding='utf-8')
index = index_path.read_text(encoding='utf-8')

# Backend: category privileges.
main = re.sub(
    r"ALL_ACCESS_MODULES = VALID_MODULES \+ \['analytics', 'timetables', SEATING_MODULE, 'assistant'\]",
    """ACCESS_HEADS = ['homepage', 'administrations', 'examination']
MODULE_HEAD = {
    'analytics': 'homepage', 'assistant': 'homepage', 'students': 'homepage',
    'teachers': 'homepage', 'classrooms': 'homepage', 'users': 'homepage',
    'attendance': 'administrations', 'syllabus': 'administrations',
    'timetables': 'administrations', 'fees': 'administrations',
    'seating': 'examination', 'invigilation': 'examination',
}
ALL_ACCESS_MODULES = ACCESS_HEADS""",
    main, count=1,
)

# Backend: every leaf endpoint now checks its parent head privilege.
main = re.sub(
    r'def check_module_access\(institute: "CurrentInstitute", module: str\):.*?\n\n\ndef get_current_institute',
    '''def check_module_access(institute: "CurrentInstitute", module: str):
    # Owners can use everything. Staff receive only category privileges.
    if institute.is_owner:
        return
    head = MODULE_HEAD.get(module, module)
    if head not in institute.allowed_modules:
        raise HTTPException(status_code=403, detail=f"Your account does not have access to the {module.title()} module")


def get_current_institute''',
    main, count=1, flags=re.S,
)

# Backend: only the three new heads may be saved as staff privileges.
main = re.sub(
    r'def _validate_modules\(modules: list\):.*?\n\n\n@app\.get\("/api/users"\)',
    '''def _validate_modules(modules: list):
    if not isinstance(modules, list):
        raise HTTPException(status_code=400, detail="Module privileges must be a list")
    bad = [m for m in modules if m not in ACCESS_HEADS]
    if bad:
        raise HTTPException(status_code=400, detail="Module privileges must be Homepage, Administrations, or Examination")


@app.get("/api/users")''',
    main, count=1, flags=re.S,
)

# Backend: legacy timetable config column is nullable if it exists.
if 'ALTER COLUMN config DROP NOT NULL' not in main:
    marker = '        "ALTER TABLE timetable_configs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",'
    migration = '''        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'timetable_configs'
                  AND column_name = 'config'
            ) THEN
                EXECUTE 'ALTER TABLE timetable_configs ALTER COLUMN config DROP NOT NULL';
            END IF;
        END $$;""",'''
    if marker in main:
        main = main.replace(marker, marker + '\n' + migration, 1)

# Backend: fix psycopg2 execute(...).fetchall() in exam seating.
bad_seating = '''    students = conn.cursor(); students.execute(
        """SELECT id, name, COALESCE(batch, '') AS batch, COALESCE(roll_number, '') AS roll_number
           FROM students WHERE branch_id = %s ORDER BY LOWER(COALESCE(batch, '')), LOWER(COALESCE(name, ''))""",
        (req.branch_id,),
    ).fetchall()
    student_rows = [dict(r) for r in students]'''
good_seating = '''    students_cur = conn.cursor()
    students_cur.execute(
        """SELECT id, name, COALESCE(batch, '') AS batch, COALESCE(roll_number, '') AS roll_number
           FROM students WHERE branch_id = %s ORDER BY LOWER(COALESCE(batch, '')), LOWER(COALESCE(name, ''))""",
        (req.branch_id,),
    )
    student_rows = [dict(r) for r in students_cur.fetchall()]'''
if bad_seating in main:
    main = main.replace(bad_seating, good_seating, 1)

main_path.write_text(main, encoding='utf-8')

# Frontend: replace the real sidebar in index.html.
nav_pattern = re.compile(r'<nav class="w-72 border-r gold-border bg-\[#0b0b0b\] flex flex-col py-6 space-y-1\.5 shrink-0">.*?</nav>', re.S)
nav = '''<nav id="moduleSidebar" class="w-72 border-r gold-border bg-[#0b0b0b] flex flex-col py-6 space-y-1.5 shrink-0">
    <div class="px-6 pb-1 elegant-font text-lg font-bold gold-gradient-text tracking-wide">ALGORITHMIC</div>
    <div class="px-6 pb-2 text-[11px] font-bold text-gray-500 uppercase tracking-widest">Enterprise Modules</div>
    <div class="px-3 space-y-1" id="moduleGroups"></div>
    <div class="mt-auto px-6 pt-6 border-t gold-border text-[11px] text-gray-400 space-y-1 bg-[#090909]">
        <p class="text-gray-300">Founded by <a href="https://machsevenstudios-website.onrender.com" target="_blank" class="gold-gradient-text hover:underline">MachSevenStudios</a></p>
        <p class="text-[10px] text-yellow-600 font-bold uppercase tracking-widest pt-1">Powered by Metasys<sup>®</sup></p>
    </div>
</nav>'''
index, n = nav_pattern.subn(nav, index, count=1)
if n != 1:
    raise SystemExit('sidebar replacement failed')

index = index.replace(
    "const ALL_MODULES = ['analytics', 'students', 'teachers', 'classrooms', 'syllabus', 'attendance', 'timetables', 'seating', 'invigilation', 'fees', 'assistant'];",
    """const ACCESS_HEADS = ['homepage', 'administrations', 'examination'];
const HEAD_MODULES = {
    homepage: [['analytics', '◈', 'Analytics'], ['assistant', '✦', 'Parallax'], ['students', '🎓', 'Student Department'], ['teachers', '👨‍🏫', 'Teacher Department'], ['classrooms', '🏛️', 'Classroom Department'], ['users', '🔐', 'Manage Users']],
    administrations: [['attendance', '📋', 'Attendance'], ['syllabus', '📚', 'Syllabus'], ['timetables', '🕒', 'Timetable'], ['fees', '💳', 'Fees']],
    examination: [['seating', '🪑', 'Exam Seating'], ['invigilation', '🛡️', 'Exam Invigilation']]
};
const MODULE_HEAD = Object.fromEntries(Object.entries(HEAD_MODULES).flatMap(([h, ms]) => ms.map(([m]) => [m, h])));"""
)

index = re.sub(
    r'        function applyIdentity\(data\) \{.*?\n        \}\n\n        function completeAuth',
    '''        function applyIdentity(data) {
            isOwner = !!data.is_owner;
            myPermission = data.permission || 'owner';
            myFullName = data.full_name || data.institute_name || '';
            myDesignation = data.designation || (isOwner ? 'Owner' : 'Staff');
            myAllowedModules = isOwner ? ACCESS_HEADS.slice() : (Array.isArray(data.allowed_modules) ? data.allowed_modules.filter(x => ACCESS_HEADS.includes(x)) : []);
            document.getElementById('headerInstituteName').textContent = data.institute_name;
            document.getElementById('headerFullName').textContent = data.full_name || data.institute_name;
            renderModuleGroups();
            const badge = document.getElementById('headerPermBadge');
            if (isOwner) badge.innerHTML = '<span class="perm-badge perm-edit">Owner</span>';
            else {
                const accessBadge = myPermission === 'read_only' ? '<span class="perm-badge perm-readonly">Read Only</span>' : '<span class="perm-badge perm-edit">Edit Access</span>';
                badge.innerHTML = `<span class="perm-badge perm-readonly">${esc(myDesignation)}</span> ${accessBadge}`;
            }
        }

        function completeAuth''',
    index, count=1, flags=re.S,
)

index = re.sub(
    r'        async function switchModule\(moduleName, clickedButton = null\) \{.*?\n        \}\n\n        async function initApp',
    '''        async function switchModule(moduleName, clickedButton = null) {
            const head = MODULE_HEAD[moduleName] || moduleName;
            if (!isOwner && !myAllowedModules.includes(head)) {
                alert('Your account does not have access to that module.');
                return;
            }
            currentModule = moduleName;
            document.querySelectorAll('.sidebar-item').forEach(btn => btn.classList.remove('active'));
            if (clickedButton) clickedButton.classList.add('active');
            else {
                const fallback = document.querySelector(`[data-module="${moduleName}"]`);
                if (fallback) fallback.classList.add('active');
            }
            await refreshCurrentModule();
        }

        function toggleModuleHead(head) {
            const panel = document.getElementById(`head-${head}`);
            const button = document.getElementById(`head-btn-${head}`);
            if (!panel || !button) return;
            panel.classList.toggle('hidden');
            button.querySelector('.head-chevron').classList.toggle('rotate-180');
        }

        function renderModuleGroups() {
            const groups = document.getElementById('moduleGroups');
            if (!groups) return;
            groups.innerHTML = ACCESS_HEADS.filter(h => isOwner || myAllowedModules.includes(h)).map(head => {
                const open = head === 'homepage' && isOwner;
                const children = HEAD_MODULES[head].filter(([m]) => m !== 'users' || isOwner);
                return `<div class="mb-1">
                    <button id="head-btn-${head}" onclick="toggleModuleHead('${head}')" class="w-full flex items-center justify-between px-3 py-3 rounded-lg text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-yellow-500 hover:bg-[#111]">
                        <span>${head === 'homepage' ? 'Homepage' : head === 'administrations' ? 'Administrations' : 'Examination'}</span><span class="head-chevron transition-transform ${open ? 'rotate-180' : ''}">⌄</span>
                    </button>
                    <div id="head-${head}" class="${open ? '' : 'hidden'} space-y-0.5">
                        ${children.map(([m, icon, label]) => `<button data-module="${m}" onclick="switchModule('${m}', this)" class="sidebar-item w-full text-left px-6 py-2.5 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>${icon}</span><span>${label}</span></button>`).join('')}
                    </div>
                </div>`;
            }).join('');
        }

        async function initApp''',
    index, count=1, flags=re.S,
)

# Manage Users: normalize the API response to an array.
index = index.replace(
    "            usersCache = parsed;\n            const tbody = document.getElementById('usersTableBody');",
    "            usersCache = Array.isArray(parsed) ? parsed : (Array.isArray(parsed?.users) ? parsed.users : []);\n            const tbody = document.getElementById('usersTableBody');",
    1,
)

# Manage Users: only category privileges are assignable; Homepage is boss-only.
index = re.sub(
    r'        function renderModuleCheckboxGrid\(checkedModules\) \{.*?\n        \}\n\n        function openUserModal',
    '''        function renderModuleCheckboxGrid(checkedModules) {
            const grid = document.getElementById('newUserModuleGrid');
            const checked = new Set(Array.isArray(checkedModules) ? checkedModules : []);
            grid.innerHTML = ACCESS_HEADS.map(m => `
                <label class="flex items-center space-x-2 text-xs text-gray-300 bg-[#0c0c0c] border gold-border rounded-lg px-3 py-2 ${m === 'homepage' ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}">
                    <input type="checkbox" class="module-check newUserModuleCheckbox" value="${m}" ${m === 'homepage' ? 'disabled' : ''} ${checked.has(m) ? 'checked' : ''}>
                    <span>${m === 'homepage' ? 'Homepage (Boss Only)' : m === 'administrations' ? 'Administrations' : 'Examination'}</span>
                </label>
            `).join('');
        }

        function openUserModal''',
    index, count=1, flags=re.S,
)

# Attendance history: stringify legacy object values instead of rendering [object Object].
history_pattern = re.compile(r'\$\{data\.history\.map\(h => `.*?`\)\.join\(\'\'\)\}', re.S)
history_replacement = '''${data.history.map(h => {
                            const date = typeof h.date === 'object' ? JSON.stringify(h.date) : String(h.date ?? '');
                            const status = typeof h.status === 'object' ? (h.status?.status || h.status?.value || JSON.stringify(h.status)) : String(h.status ?? '');
                            return `
                            <div class="flex justify-between items-center py-2 text-sm">
                                <span class="text-gray-300">${esc(date)}</span>
                                <span class="font-semibold ${status === 'Present' ? 'text-green-400' : 'text-red-400'}">${esc(status)}</span>
                            </div>
                        `;
                    }).join('')}'''
index, _ = history_pattern.subn(history_replacement, index, count=1)

index = index.replace('</style>', '.head-chevron { transition: transform .15s ease; } .rotate-180 { transform: rotate(180deg); }\n    </style>', 1)
index_path.write_text(index, encoding='utf-8')
