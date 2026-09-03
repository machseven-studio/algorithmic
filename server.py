"""Production wrapper for the ALGORITHMIC application.

The original application is intentionally left intact; this module applies the
backwards-compatible database migration, replaces the few broken endpoints,
and serves the existing frontend with a small navigation/permissions patch.
"""

import json
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute

import main


# ---------------------------------------------------------------------------
# Permission model
# ---------------------------------------------------------------------------

ACCESS_HEADS = ["homepage", "administrations", "examination"]
HEAD_MODULES = {
    "homepage": {"analytics", "assistant", "students", "teachers", "classrooms", "users"},
    "administrations": {"attendance", "syllabus", "timetables", "fees"},
    "examination": {"seating", "invigilation"},
}
MODULE_TO_HEAD = {
    module: head for head, modules in HEAD_MODULES.items() for module in modules
}

# Keep the original application's public constant aligned with the new
# privilege vocabulary. Existing staff rows are normalized below.
main.ALL_ACCESS_MODULES = ACCESS_HEADS


def _normalize_access(value):
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    return [x for x in value if x in ACCESS_HEADS]


def _check_module_access(institute, module: str):
    if institute.is_owner:
        return
    required_head = MODULE_TO_HEAD.get(module, module)
    allowed = set(_normalize_access(institute.allowed_modules))
    if required_head not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Your account does not have access to the {module.title()} module",
        )


# Existing route handlers call main.check_module_access at request time, so
# replacing the helper fixes every protected endpoint without duplicating the
# whole application.
main.check_module_access = _check_module_access


# ---------------------------------------------------------------------------
# Database compatibility migration
# ---------------------------------------------------------------------------

def _run_compatibility_migrations():
    conn = main.get_conn()
    try:
        cur = conn.cursor()

        # Older deployments created timetable_configs with a legacy `config`
        # column that was NOT NULL. The current generator stores its normalized
        # values in timings_json + teachers_config_json, so the obsolete column
        # must be nullable for existing databases.
        cur.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'timetable_configs'
                      AND column_name = 'config'
                ) THEN
                    EXECUTE 'ALTER TABLE timetable_configs ALTER COLUMN config DROP NOT NULL';
                END IF;
            END $$;
            """
        )

        # The privilege system used to persist individual leaf-module names.
        # Those privileges are deliberately retired. Existing users therefore
        # start with no category privileges rather than silently inheriting the
        # old model. Category-based rows are preserved across restarts.
        cur.execute("SELECT id, module_access FROM staff_users")
        for row in cur.fetchall():
            raw = row["module_access"]
            normalized = _normalize_access(raw)
            legacy_present = False
            try:
                parsed = json.loads(raw) if raw else []
                legacy_present = isinstance(parsed, list) and any(x not in ACCESS_HEADS for x in parsed)
            except (TypeError, ValueError, json.JSONDecodeError):
                legacy_present = True
            if legacy_present or raw is None:
                cur.execute(
                    "UPDATE staff_users SET module_access = %s WHERE id = %s",
                    (json.dumps(normalized if not legacy_present else []), row["id"]),
                )

        conn.commit()
    finally:
        conn.close()


_run_compatibility_migrations()


# ---------------------------------------------------------------------------
# Fixed exam seating generation
# ---------------------------------------------------------------------------

def _generate_seating_impl_fixed(req, institute):
    _check_module_access(institute, main.SEATING_MODULE)
    main.verify_branch_ownership(req.branch_id, institute.id)
    if req.rows < 1 or req.columns < 1:
        raise HTTPException(status_code=400, detail="Rows and columns must both be at least 1.")
    room_number = req.room_number.strip()
    if not room_number:
        raise HTTPException(status_code=400, detail="Room number is required.")

    conn = main.get_conn()
    try:
        room_cur = conn.cursor()
        room_cur.execute(
            "SELECT room_no, capacity FROM classrooms WHERE branch_id = %s AND room_no = %s",
            (req.branch_id, room_number),
        )
        room = room_cur.fetchone()
        if not room:
            raise HTTPException(status_code=400, detail="Selected exam room is not registered in this branch.")

        requested_capacity = req.rows * req.columns
        room_capacity = int(room[1] or 0)
        if room_capacity <= 0:
            raise HTTPException(status_code=400, detail="Selected room has no valid seating capacity.")
        if requested_capacity > room_capacity:
            raise HTTPException(
                status_code=400,
                detail=f"Grid capacity ({requested_capacity}) exceeds room capacity ({room_capacity}).",
            )

        # FIX: cursor.execute() returns None in psycopg2. The old implementation
        # chained `.fetchall()` onto execute(), producing the reported
        # "NoneType has no attribute fetchall" error.
        students_cur = conn.cursor()
        students_cur.execute(
            """
            SELECT id, name, COALESCE(batch, '') AS batch,
                   COALESCE(roll_number, '') AS roll_number
            FROM students
            WHERE branch_id = %s
            ORDER BY LOWER(COALESCE(batch, '')), LOWER(COALESCE(name, ''))
            """,
            (req.branch_id,),
        )
        student_rows = [dict(r) for r in students_cur.fetchall()]
        assignments = main._build_seating_layout(student_rows, req.rows, req.columns)

        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM exam_seatings WHERE branch_id = %s AND exam_date = %s AND room_number = %s",
            (req.branch_id, req.exam_date, room_number),
        )
        cursor.execute(
            """
            INSERT INTO exam_seatings
                (branch_id, exam_date, room_number, rows, columns, assignments_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                req.branch_id,
                req.exam_date,
                room_number,
                req.rows,
                req.columns,
                json.dumps(assignments),
                datetime.utcnow().isoformat(),
            ),
        )
        layout_id = cursor.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    main.audit_write(
        institute,
        req.branch_id,
        "GENERATE_SEATING",
        None,
        {
            "id": layout_id,
            "exam_date": req.exam_date,
            "room_number": room_number,
            "rows": req.rows,
            "columns": req.columns,
            "assignments": assignments,
        },
    )
    return {"status": "success", "id": layout_id, "assignments": assignments}


main._generate_seating_impl = _generate_seating_impl_fixed


# ---------------------------------------------------------------------------
# Fixed attendance-history response
# ---------------------------------------------------------------------------

def _display_scalar(value):
    """Turn legacy JSON/object values into a human-readable scalar."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("status", "value", "name", "text", "label"):
            if key in value and not isinstance(value[key], (dict, list)):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, list):
        return ", ".join(_display_scalar(v) for v in value)
    return str(value)


def get_attendance_history_fixed(
    branch_id: int,
    student_name: str,
    institute=Depends(main.get_current_institute),
):
    _check_module_access(institute, "attendance")
    main.verify_branch_read_access(branch_id, institute.id)
    conn = main.get_conn()
    try:
        cursor = conn.cursor()
        if branch_id == 0:
            cursor.execute(
                """
                SELECT date, status
                FROM attendance
                WHERE branch_id IN (SELECT id FROM branches WHERE tenant_id = %s)
                  AND student_name = %s
                ORDER BY date DESC
                """,
                (institute.id, student_name),
            )
        else:
            cursor.execute(
                """
                SELECT date, status
                FROM attendance
                WHERE branch_id = %s AND student_name = %s
                ORDER BY date DESC
                """,
                (branch_id, student_name),
            )
        raw_history = cursor.fetchall()
    finally:
        conn.close()

    history = [
        {"date": _display_scalar(row["date"]), "status": _display_scalar(row["status"])}
        for row in raw_history
    ]
    present = sum(1 for item in history if item["status"].strip().lower() == "present")
    return {
        "student_name": student_name,
        "history": history,
        "total_marked": len(history),
        "present_count": present,
        "absent_count": len(history) - present,
    }


# Replace the registered attendance-history route. FastAPI evaluates matching
# path operations in registration order, so the old route must be removed.
for route in list(main.app.router.routes):
    if isinstance(route, APIRoute) and route.path == "/api/attendance/history/{branch_id}":
        main.app.router.routes.remove(route)

main.app.add_api_route(
    "/api/attendance/history/{branch_id}",
    get_attendance_history_fixed,
    methods=["GET"],
)


# ---------------------------------------------------------------------------
# Owner-only Manage Users remains owner-only, but accepts the new categories.
# ---------------------------------------------------------------------------

main._validate_modules = lambda modules: (
    None
    if isinstance(modules, list) and all(m in ACCESS_HEADS for m in modules)
    else (_ for _ in ()).throw(
        HTTPException(
            status_code=400,
            detail="Module privileges must use Homepage, Administrations, or Examination.",
        )
    )
)


# ---------------------------------------------------------------------------
# Frontend patch
# ---------------------------------------------------------------------------

FRONTEND_PATCH = r"""
<style>
  .alg-head-btn {
    width: 100%; display: flex; align-items: center; justify-content: space-between;
    padding: .72rem 1.25rem; color: #9ca3af; background: transparent; border: 0;
    text-align: left; font-size: 10px; font-weight: 900; letter-spacing: .14em;
    text-transform: uppercase; cursor: pointer;
  }
  .alg-head-btn:hover { color: #e8c767; background: rgba(212,175,55,.06); }
  .alg-head-chevron { transition: transform .15s ease; }
  .alg-head-btn.open .alg-head-chevron { transform: rotate(180deg); }
  .alg-head-items { overflow: hidden; }
  .alg-head-items.collapsed { display: none; }
  .alg-child { padding-left: 2.35rem !important; font-size: 10px !important; }
  .alg-privilege-note { color: #6b7280; font-size: 9px; margin-top: -2px; }
</style>
<script>
(() => {
  const ACCESS_HEADS = ['homepage', 'administrations', 'examination'];
  const HEAD_LABELS = {
    homepage: 'Homepage', administrations: 'Administrations', examination: 'Examination'
  };
  const HEAD_CHILDREN = {
    homepage: [
      ['analytics', '◈', 'Analytics'],
      ['assistant', '✦', 'Parallax'],
      ['students', '🎓', 'Student Department'],
      ['teachers', '👨‍🏫', 'Teacher Department'],
      ['classrooms', '🏛️', 'Classroom Department'],
      ['users', '🔐', 'Manage Users']
    ],
    administrations: [
      ['attendance', '📋', 'Attendance'],
      ['syllabus', '📚', 'Syllabus'],
      ['timetables', '🕒', 'Timetable'],
      ['fees', '💳', 'Fees']
    ],
    examination: [
      ['seating', '🪑', 'Exam Seating'],
      ['invigilation', '🛡️', 'Exam Invigilation']
    ]
  };
  const MODULE_HEAD = {
    analytics: 'homepage', assistant: 'homepage', students: 'homepage', teachers: 'homepage',
    classrooms: 'homepage', users: 'homepage', attendance: 'administrations', syllabus: 'administrations',
    timetables: 'administrations', fees: 'administrations', seating: 'examination', invigilation: 'examination'
  };

  function hasHeadAccess(head) {
    return !!isOwner || (Array.isArray(myAllowedModules) && myAllowedModules.includes(head));
  }

  window.buildGroupedSidebar = function() {
    const nav = document.querySelector('#appContainer nav');
    if (!nav) return;
    const visibleHeads = ACCESS_HEADS.filter(hasHeadAccess);
    let html = `
      <div class="px-6 pb-1 elegant-font text-lg font-bold gold-gradient-text tracking-wide">ALGORITHMIC</div>
      <div class="px-6 pb-2 text-[11px] font-bold text-gray-500 uppercase tracking-widest">Enterprise Modules</div>
    `;
    for (const head of visibleHeads) {
      const open = head === (MODULE_HEAD[currentModule] || (isOwner ? 'homepage' : null));
      html += `
        <div class="alg-head-group" data-head="${head}">
          <button type="button" class="alg-head-btn ${open ? 'open' : ''}" onclick="toggleModuleHead('${head}')">
            <span>${HEAD_LABELS[head]}</span><span class="alg-head-chevron">⌄</span>
          </button>
          <div class="alg-head-items ${open ? '' : 'collapsed'}">
            ${HEAD_CHILDREN[head].map(([module, icon, label]) => `
              <button data-module="${module}" onclick="switchModule('${module}', this)"
                class="sidebar-item alg-child w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3 ${currentModule === module ? 'active' : ''}">
                <span>${icon}</span><span>${label}</span>
              </button>
            `).join('')}
          </div>
        </div>
      `;
    }
    if (!visibleHeads.length) {
      html += `<div class="px-6 py-4 text-[10px] text-gray-600 uppercase tracking-widest">No module privileges assigned</div>`;
    }
    html += `
      <div class="mt-auto px-6 pt-6 border-t gold-border text-[11px] text-gray-400 space-y-1 bg-[#090909]">
        <p class="text-gray-300">Founded by <a href="https://machsevenstudios-website.onrender.com" target="_blank" class="gold-gradient-text hover:underline">MachSevenStudios</a></p>
        <p class="text-[10px] text-yellow-600 font-bold uppercase tracking-widest pt-1">Powered by Metasys<sup>®</sup></p>
      </div>`;
    nav.innerHTML = html;
  };

  window.toggleModuleHead = function(head) {
    const group = document.querySelector(`.alg-head-group[data-head="${head}"]`);
    if (!group) return;
    const btn = group.querySelector('.alg-head-btn');
    const items = group.querySelector('.alg-head-items');
    const collapsed = items.classList.toggle('collapsed');
    btn.classList.toggle('open', !collapsed);
  };

  window.switchModule = async function(moduleName, clickedButton = null) {
    if (moduleName === 'users' && !isOwner) {
      alert('Only the boss can access Manage Users.');
      return;
    }
    const head = MODULE_HEAD[moduleName];
    if (!isOwner && !head || (!isOwner && !myAllowedModules.includes(head))) {
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
  };

  window.applyIdentity = function(data) {
    isOwner = !!data.is_owner;
    myPermission = data.permission || 'owner';
    myFullName = data.full_name || data.institute_name || '';
    myDesignation = data.designation || (isOwner ? 'Owner' : 'Staff');
    myAllowedModules = isOwner ? ACCESS_HEADS.slice() : (Array.isArray(data.allowed_modules) ? data.allowed_modules.filter(x => ACCESS_HEADS.includes(x)) : []);
    document.getElementById('headerInstituteName').textContent = data.institute_name || '—';
    document.getElementById('headerFullName').textContent = data.full_name || data.institute_name || '—';
    const badge = document.getElementById('headerPermBadge');
    if (badge) {
      badge.innerHTML = isOwner
        ? '<span class="perm-badge perm-edit">Owner</span>'
        : `<span class="perm-badge perm-readonly">${esc(myDesignation)}</span> <span class="perm-badge ${myPermission === 'read_only' ? 'perm-readonly' : 'perm-edit'}">${myPermission === 'read_only' ? 'Read Only' : 'Edit Access'}</span>`;
    }
    buildGroupedSidebar();
  };

  window.loadUsers = async function() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    const res = await authFetch('/api/users');
    const text = await res.text();
    let parsed;
    try { parsed = text ? JSON.parse(text) : []; }
    catch (_) {
      tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-red-400">Failed to load users: invalid server response (HTTP ${res.status}).</td></tr>`;
      return;
    }
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-red-400">${esc(parsed && parsed.detail || 'Failed to load users.')}</td></tr>`;
      return;
    }
    // Never call .map() on an arbitrary response object. Accept the legacy
    // {users:[...]} shape too, while preferring the canonical array response.
    usersCache = Array.isArray(parsed) ? parsed : (Array.isArray(parsed.users) ? parsed.users : []);
    if (!Array.isArray(usersCache)) usersCache = [];
    if (!usersCache.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="p-8 text-center text-gray-500">No staff users yet. Click '+ Add User' to grant access.</td></tr>`;
      return;
    }
    tbody.innerHTML = usersCache.map(u => `
      <tr class="border-b border-gray-900 hover:bg-[#121212] fast-transition">
        <td class="p-4 font-medium text-white">${esc(u.full_name || '')}</td>
        <td class="p-4 text-gray-400">${esc(u.email || '')}</td>
        <td class="p-4"><span class="perm-badge perm-readonly">${esc(u.designation || 'Staff')}</span></td>
        <td class="p-4 text-xs text-gray-400">${(Array.isArray(u.modules) ? u.modules : []).map(m => esc(HEAD_LABELS[m] || m)).join(', ') || '<span class="text-gray-600">None</span>'}</td>
        <td class="p-4">
          <select onchange="changeUserPermission(${u.id}, this.value)" class="bg-[#0c0c0c] border gold-border rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none">
            <option value="edit" ${u.permission === 'edit' ? 'selected' : ''}>Edit Access</option>
            <option value="read_only" ${u.permission === 'read_only' ? 'selected' : ''}>Read Only</option>
          </select>
        </td>
        <td class="p-4 text-right whitespace-nowrap">
          <button onclick="openUserModal(${u.id})" title="Edit access" class="row-delete-btn fast-transition text-sm leading-none mr-3">✎</button>
          <button onclick="removeUser(${u.id})" title="Revoke access" class="row-delete-btn fast-transition text-lg leading-none">🗑</button>
        </td>
      </tr>`).join('');
  };

  window.renderModuleCheckboxGrid = function(checkedModules) {
    const grid = document.getElementById('newUserModuleGrid');
    if (!grid) return;
    const checked = new Set(Array.isArray(checkedModules) ? checkedModules : []);
    grid.innerHTML = `
      <label class="flex items-center space-x-2 text-xs text-gray-300 bg-[#0c0c0c] border gold-border rounded-lg px-3 py-2 cursor-not-allowed opacity-60">
        <input type="checkbox" disabled value="homepage" class="module-check newUserModuleCheckbox">
        <span>Homepage</span><span class="alg-privilege-note">Boss only</span>
      </label>
      <label class="flex items-center space-x-2 text-xs text-gray-300 bg-[#0c0c0c] border gold-border rounded-lg px-3 py-2 cursor-pointer">
        <input type="checkbox" class="module-check newUserModuleCheckbox" value="administrations" ${checked.has('administrations') ? 'checked' : ''}>
        <span>Administrations</span>
      </label>
      <label class="flex items-center space-x-2 text-xs text-gray-300 bg-[#0c0c0c] border gold-border rounded-lg px-3 py-2 cursor-pointer">
        <input type="checkbox" class="module-check newUserModuleCheckbox" value="examination" ${checked.has('examination') ? 'checked' : ''}>
        <span>Examination</span>
      </label>`;
  };

  window.openUserModal = function(userId) {
    const isEdit = userId !== undefined && userId !== null;
    const existing = isEdit ? usersCache.find(u => u.id === userId) : null;
    document.getElementById('userModalTitle').textContent = isEdit ? 'Edit User Access' : 'Add User';
    document.getElementById('userModalSubmitBtn').textContent = isEdit ? 'Save Changes' : 'Create User';
    document.getElementById('newUserName').value = existing ? existing.full_name : '';
    document.getElementById('newUserName').disabled = isEdit;
    document.getElementById('newUserEmail').value = existing ? existing.email : '';
    document.getElementById('newUserEmail').disabled = isEdit;
    document.getElementById('newUserPassword').value = '';
    document.getElementById('newUserPassword').placeholder = isEdit ? 'Password cannot be changed here' : 'Password (min 8 characters)';
    document.getElementById('newUserPassword').disabled = isEdit;
    document.getElementById('newUserDesignation').value = existing ? (existing.designation || '') : '';
    document.getElementById('newUserPermission').value = existing ? existing.permission : 'edit';
    renderModuleCheckboxGrid(existing ? existing.modules : []);
    document.getElementById('userModalError').textContent = '';
    document.getElementById('userModal').classList.remove('hidden');
    window.activeUserModalId = isEdit ? userId : null;
  };

  window.submitUserForm = async function() {
    const errorEl = document.getElementById('userModalError');
    const userId = window.activeUserModalId;
    const designation = document.getElementById('newUserDesignation').value.trim();
    const permission = document.getElementById('newUserPermission').value;
    const modules = Array.from(document.querySelectorAll('.newUserModuleCheckbox:checked')).map(el => el.value);
    if (!designation) { errorEl.textContent = 'Designation is required.'; return; }
    if (userId) {
      const res = await authFetch(`/api/users/${userId}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ designation, permission, modules })
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { closeUserModal(); await loadUsers(); }
      else errorEl.textContent = data.detail || 'Failed to update user.';
      return;
    }
    const full_name = document.getElementById('newUserName').value.trim();
    const email = document.getElementById('newUserEmail').value.trim();
    const password = document.getElementById('newUserPassword').value;
    if (!full_name || !email || !password) { errorEl.textContent = 'All fields are required.'; return; }
    const res = await authFetch('/api/users', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ full_name, email, password, permission, designation, modules })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { closeUserModal(); await loadUsers(); }
    else errorEl.textContent = data.detail || 'Failed to create user.';
  };

  // Rebuild the grouped navigation immediately on initial page load as well as
  // after login. Owner identity is already represented by isOwner=true.
  const boot = () => { if (document.getElementById('appContainer')) buildGroupedSidebar(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
</script>
"""


def _patched_root():
    html = main.HTML_CONTENT
    marker = "</body>"
    if marker in html:
        html = html.replace(marker, FRONTEND_PATCH + marker, 1)
    else:
        html += FRONTEND_PATCH
    return HTMLResponse(content=html, status_code=200)


# Replace the original root route so the grouped navigation is part of the
# actual HTML delivered by Render.
for route in list(main.app.router.routes):
    if isinstance(route, APIRoute) and route.path == "/" and "GET" in route.methods:
        main.app.router.routes.remove(route)

main.app.add_api_route("/", _patched_root, methods=["GET"], response_class=HTMLResponse)


# Export the exact FastAPI application Render should run.
app = main.app
