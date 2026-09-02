import os
import hashlib
import secrets
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from io import BytesIO

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, EmailStr
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = FastAPI(title="ALGORITHMIC", version="5.0.1")
IST = ZoneInfo("Asia/Kolkata")
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required. Configure PostgreSQL in Render/local environment.")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

VALID_MODULES = ["students", "teachers", "classrooms", "syllabus", "timetable", "attendance", "invigilation", "fees", "seating"]


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def q(sql, params=(), one=False, all_rows=False):
    with db() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            if one:
                return cur.fetchone()
            if all_rows:
                return cur.fetchall()
            return None


def init_db():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS institutes (id BIGSERIAL PRIMARY KEY, institute_name TEXT NOT NULL, full_name TEXT, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, password_salt TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        c.execute("CREATE TABLE IF NOT EXISTS staff_users (id BIGSERIAL PRIMARY KEY, institute_id BIGINT NOT NULL REFERENCES institutes(id) ON DELETE CASCADE, full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, password_salt TEXT NOT NULL, designation TEXT NOT NULL DEFAULT 'admin', permissions JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        c.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, institute_id BIGINT NOT NULL REFERENCES institutes(id) ON DELETE CASCADE, staff_user_id BIGINT REFERENCES staff_users(id) ON DELETE CASCADE, expires_at TIMESTAMPTZ NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS branches (id BIGSERIAL PRIMARY KEY, institute_id BIGINT NOT NULL REFERENCES institutes(id) ON DELETE CASCADE, name TEXT NOT NULL, UNIQUE(institute_id,name))")
        c.execute("CREATE TABLE IF NOT EXISTS students (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, name TEXT NOT NULL, email TEXT, batch TEXT, status TEXT DEFAULT 'Active', document TEXT, roll_number TEXT, parent_contact TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS teachers (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, name TEXT NOT NULL, subject TEXT, department TEXT, document TEXT, contact_number TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS classrooms (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, room_no TEXT, capacity INTEGER, building TEXT, document TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS syllabus (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, subject TEXT, semester TEXT, units INTEGER, document TEXT, topic TEXT, teacher_name TEXT, num_lectures INTEGER, lecture_date DATE)")
        c.execute("CREATE TABLE IF NOT EXISTS attendance (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, student_id BIGINT REFERENCES students(id) ON DELETE SET NULL, student_name TEXT NOT NULL, batch TEXT, date DATE NOT NULL, status TEXT NOT NULL, document TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS timetable_configs (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, batch_name TEXT NOT NULL, config JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(branch_id,batch_name))")
        c.execute("CREATE TABLE IF NOT EXISTS timetable_slots (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, batch_name TEXT NOT NULL, day TEXT NOT NULL, time_slot TEXT NOT NULL, lecture_number INTEGER NOT NULL, subject TEXT, teacher TEXT, room TEXT, generation_id TEXT NOT NULL, UNIQUE(branch_id,batch_name,day,time_slot))")
        c.execute("CREATE TABLE IF NOT EXISTS invigilation (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, teacher_name TEXT, exam_date DATE, room TEXT, document TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS fees (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, student_id BIGINT REFERENCES students(id) ON DELETE SET NULL, student_name TEXT, batch TEXT, amount_inr NUMERIC(12,2), status TEXT, due_date DATE, document TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS seating (id BIGSERIAL PRIMARY KEY, branch_id BIGINT REFERENCES branches(id) ON DELETE CASCADE, exam_name TEXT, exam_date DATE, room TEXT, student_name TEXT, seat_number TEXT)")
        c.execute("ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS designation TEXT NOT NULL DEFAULT 'admin'")
        c.execute("ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '{}'::jsonb")
        c.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_id BIGINT REFERENCES students(id) ON DELETE SET NULL")
        c.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS batch TEXT")
        c.execute("ALTER TABLE fees ADD COLUMN IF NOT EXISTS student_id BIGINT REFERENCES students(id) ON DELETE SET NULL")
        c.execute("ALTER TABLE fees ADD COLUMN IF NOT EXISTS batch TEXT")
        c.execute("ALTER TABLE timetable_slots ADD COLUMN IF NOT EXISTS lecture_number INTEGER NOT NULL DEFAULT 1")
        c.commit()


init_db()


def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def session_token(institute_id, staff_id=None):
    t = secrets.token_urlsafe(32)
    q("INSERT INTO sessions(token,institute_id,staff_user_id,expires_at) VALUES(%s,%s,%s,%s)", (t, institute_id, staff_id, datetime.now(IST) + timedelta(days=7)))
    return t


def current(auth: str = Header(None)):
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    s = q("SELECT s.*, i.institute_name, i.full_name AS owner_name, i.email AS owner_email FROM sessions s JOIN institutes i ON i.id=s.institute_id WHERE s.token=%s", (auth.split(" ", 1)[1],), one=True)
    if not s or s["expires_at"] < datetime.now(IST):
        raise HTTPException(401, "Invalid or expired session")
    if s["staff_user_id"]:
        u = q("SELECT * FROM staff_users WHERE id=%s", (s["staff_user_id"],), one=True)
        if not u:
            raise HTTPException(401, "Invalid staff account")
        return {"id": s["institute_id"], "institute_name": s["institute_name"], "full_name": u["full_name"], "email": u["email"], "is_owner": False, "designation": u["designation"], "permissions": u["permissions"] or {}}
    return {"id": s["institute_id"], "institute_name": s["institute_name"], "full_name": s["owner_name"] or "", "email": s["owner_email"], "is_owner": True, "designation": "boss", "permissions": {m: "edit" for m in VALID_MODULES}}


def owner(c=Depends(current)):
    if not c["is_owner"]:
        raise HTTPException(403, "Only the boss can manage users")
    return c


def check_access(module, c, write=False):
    level = "edit" if c["is_owner"] else (c["permissions"] or {}).get(module, "none")
    allowed = ["edit"] if write else ["view", "edit"]
    if level not in allowed:
        raise HTTPException(403, f"No {'edit ' if write else ''}access to {module}")


def branch_ok(branch_id, institute_id):
    if not q("SELECT id FROM branches WHERE id=%s AND institute_id=%s", (branch_id, institute_id), one=True):
        raise HTTPException(404, "Branch not found")


class Signup(BaseModel):
    institute_name: str
    full_name: str
    email: EmailStr
    password: str


class Login(BaseModel):
    email: EmailStr
    password: str


class StaffCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    designation: str
    permissions: dict[str, str] | None = None


class StaffUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    designation: str | None = None
    permissions: dict[str, str] | None = None


class BranchIn(BaseModel):
    name: str


class Record(BaseModel):
    data: dict


class AttendanceIn(BaseModel):
    student_id: int | None = None
    student_name: str
    batch: str | None = None
    date: date
    status: str


class TimetableIn(BaseModel):
    batch_name: str
    days: list[str]
    unavailable_days: list[str] = []
    slots: list[str]
    lectures_per_week: int
    subjects: list[dict]
    teachers: list[str] = []
    rooms: list[str] = []


class TimetableEdit(BaseModel):
    day: str
    time_slot: str
    lecture_number: int
    subject: str | None = None
    teacher: str | None = None
    room: str | None = None


@app.post("/api/auth/signup")
def signup(x: Signup):
    if len(x.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    salt = secrets.token_hex(16)
    try:
        with db() as c:
            r = c.execute("INSERT INTO institutes(institute_name,full_name,email,password_hash,password_salt) VALUES(%s,%s,%s,%s,%s) RETURNING id", (x.institute_name, x.full_name, x.email.lower(), hash_pw(x.password, salt), salt)).fetchone()
            iid = r["id"]
            c.execute("INSERT INTO branches(institute_id,name) VALUES(%s,'Main Campus')", (iid,))
            c.commit()
    except Exception:
        raise HTTPException(400, "An account with this email already exists")
    return {"token": session_token(iid), "institute_name": x.institute_name, "full_name": x.full_name, "is_owner": True, "designation": "boss"}


@app.post("/api/auth/login")
def login(x: Login):
    bad = HTTPException(401, "Invalid email or password")
    u = q("SELECT * FROM institutes WHERE email=%s", (x.email.lower(),), one=True)
    if u and secrets.compare_digest(hash_pw(x.password, u["password_salt"]), u["password_hash"]):
        return {"token": session_token(u["id"]), "institute_name": u["institute_name"], "full_name": u["full_name"] or "", "is_owner": True, "designation": "boss"}
    s = q("SELECT * FROM staff_users WHERE email=%s", (x.email.lower(),), one=True)
    if s and secrets.compare_digest(hash_pw(x.password, s["password_salt"]), s["password_hash"]):
        i = q("SELECT institute_name FROM institutes WHERE id=%s", (s["institute_id"],), one=True)
        return {"token": session_token(s["institute_id"], s["id"]), "institute_name": i["institute_name"], "full_name": s["full_name"], "is_owner": False, "designation": s["designation"], "permissions": s["permissions"] or {}}
    raise bad


@app.get("/api/auth/me")
def me(c=Depends(current)):
    return c


@app.post("/api/auth/logout")
def logout(auth: str = Header(None)):
    if auth and auth.startswith("Bearer "):
        q("DELETE FROM sessions WHERE token=%s", (auth.split(" ", 1)[1],))
    return {"status": "logged out"}


@app.patch("/api/institute/name")
def institute_name(x: dict, c=Depends(current)):
    check_access("students", c, True)
    name = (x.get("institute_name") or "").strip()
    if not name:
        raise HTTPException(400, "Institute name cannot be empty")
    q("UPDATE institutes SET institute_name=%s WHERE id=%s", (name, c["id"]))
    return {"institute_name": name}


@app.get("/api/branches")
def branches(c=Depends(current)):
    return q("SELECT * FROM branches WHERE institute_id=%s ORDER BY name", (c["id"],), all_rows=True)


@app.post("/api/branches")
def add_branch(x: BranchIn, c=Depends(current)):
    check_access("students", c, True)
    return q("INSERT INTO branches(institute_id,name) VALUES(%s,%s) RETURNING *", (c["id"], x.name.strip()), one=True)


@app.get("/api/users")
def users(c=Depends(owner)):
    return q("SELECT id,full_name,email,designation,permissions,created_at FROM staff_users WHERE institute_id=%s ORDER BY full_name", (c["id"],), all_rows=True)


@app.post("/api/users")
def add_user(x: StaffCreate, c=Depends(owner)):
    if len(x.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    perms = x.permissions or {m: "none" for m in VALID_MODULES}
    salt = secrets.token_hex(16)
    try:
        return q("INSERT INTO staff_users(institute_id,full_name,email,password_hash,password_salt,designation,permissions) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id,full_name,email,designation,permissions,created_at", (c["id"], x.full_name, x.email.lower(), hash_pw(x.password, salt), salt, x.designation, Jsonb(perms)), one=True)
    except Exception:
        raise HTTPException(400, "A user with this email already exists")


@app.patch("/api/users/{uid}")
def edit_user(uid: int, x: StaffUpdate, c=Depends(owner)):
    u = q("SELECT * FROM staff_users WHERE id=%s AND institute_id=%s", (uid, c["id"]), one=True)
    if not u:
        raise HTTPException(404, "User not found")
    fields, vals = [], []
    for k in ["full_name", "email", "designation"]:
        v = getattr(x, k)
        if v is not None:
            fields.append(k + "=%s")
            vals.append(str(v).lower() if k == "email" else v)
    if x.permissions is not None:
        fields.append("permissions=%s"); vals.append(Jsonb(x.permissions))
    if x.password:
        salt = secrets.token_hex(16)
        fields += ["password_hash=%s", "password_salt=%s"]
        vals += [hash_pw(x.password, salt), salt]
    if fields:
        vals += [uid, c["id"]]
        q("UPDATE staff_users SET " + ",".join(fields) + " WHERE id=%s AND institute_id=%s", vals)
    return q("SELECT id,full_name,email,designation,permissions,created_at FROM staff_users WHERE id=%s", (uid,), one=True)


@app.delete("/api/users/{uid}")
def del_user(uid: int, c=Depends(owner)):
    q("DELETE FROM staff_users WHERE id=%s AND institute_id=%s", (uid, c["id"]))
    return {"ok": True}


TABLES = {
    "students": ("students", ["name", "email", "batch", "status", "roll_number", "parent_contact", "document"]),
    "teachers": ("teachers", ["name", "subject", "department", "contact_number", "document"]),
    "classrooms": ("classrooms", ["room_no", "capacity", "building", "document"]),
    "syllabus": ("syllabus", ["subject", "semester", "units", "topic", "teacher_name", "num_lectures", "lecture_date", "document"]),
    "attendance": ("attendance", ["student_id", "student_name", "batch", "date", "status", "document"]),
    "invigilation": ("invigilation", ["teacher_name", "exam_date", "room", "document"]),
    "fees": ("fees", ["student_id", "student_name", "batch", "amount_inr", "status", "due_date", "document"]),
    "seating": ("seating", ["exam_name", "exam_date", "room", "student_name", "seat_number"]),
}


@app.get("/api/{module}")
def list_module(module: str, branch_id: int, c=Depends(current)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    check_access(module, c)
    branch_ok(branch_id, c["id"])
    table, cols = TABLES[module]
    return q(f"SELECT id,{','.join(cols)} FROM {table} WHERE branch_id=%s ORDER BY id DESC", (branch_id,), all_rows=True)


@app.post("/api/{module}")
def add_module(module: str, branch_id: int, x: Record, c=Depends(current)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    check_access(module, c, True)
    branch_ok(branch_id, c["id"])
    table, cols = TABLES[module]
    data = {k: v for k, v in x.data.items() if k in cols}
    if not data:
        raise HTTPException(400, "No editable fields supplied")
    keys = list(data); vals = [data[k] for k in keys]
    return q(f"INSERT INTO {table}(branch_id,{','.join(keys)}) VALUES(%s,{','.join(['%s']*len(keys))}) RETURNING id,{','.join(cols)}", [branch_id, *vals], one=True)


@app.patch("/api/{module}/{rid}")
def edit_module(module: str, rid: int, x: Record, branch_id: int, c=Depends(current)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    check_access(module, c, True)
    branch_ok(branch_id, c["id"])
    table, cols = TABLES[module]
    data = {k: v for k, v in x.data.items() if k in cols}
    if not data:
        raise HTTPException(400, "No editable fields supplied")
    keys = list(data); vals = [data[k] for k in keys] + [rid, branch_id]
    q(f"UPDATE {table} SET " + ",".join(k + "=%s" for k in keys) + " WHERE id=%s AND branch_id=%s", vals)
    return q(f"SELECT id,{','.join(cols)} FROM {table} WHERE id=%s", (rid,), one=True)


@app.delete("/api/{module}/{rid}")
def delete_module(module: str, rid: int, branch_id: int, c=Depends(current)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    check_access(module, c, True)
    branch_ok(branch_id, c["id"])
    table, _ = TABLES[module]
    q(f"DELETE FROM {table} WHERE id=%s AND branch_id=%s", (rid, branch_id))
    return {"ok": True}


@app.get("/api/attendance")
def attendance(branch_id: int, c=Depends(current)):
    check_access("attendance", c)
    branch_ok(branch_id, c["id"])
    return q("SELECT id,student_id,student_name,batch,date,status,document FROM attendance WHERE branch_id=%s ORDER BY date DESC,id DESC", (branch_id,), all_rows=True)


@app.post("/api/attendance")
def add_att(x: AttendanceIn, branch_id: int, c=Depends(current)):
    check_access("attendance", c, True)
    branch_ok(branch_id, c["id"])
    return q("INSERT INTO attendance(branch_id,student_id,student_name,batch,date,status) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *", (branch_id, x.student_id, x.student_name, x.batch, x.date, x.status), one=True)


@app.patch("/api/attendance/{rid}")
def edit_att(rid: int, x: AttendanceIn, branch_id: int, c=Depends(current)):
    check_access("attendance", c, True)
    branch_ok(branch_id, c["id"])
    q("UPDATE attendance SET student_id=%s,student_name=%s,batch=%s,date=%s,status=%s WHERE id=%s AND branch_id=%s", (x.student_id, x.student_name, x.batch, x.date, x.status, rid, branch_id))
    return {"ok": True}


@app.get("/api/students/{student_id}/attendance")
def student_history(student_id: int, branch_id: int, c=Depends(current)):
    check_access("attendance", c)
    branch_ok(branch_id, c["id"])
    return q("SELECT id,date,status,batch FROM attendance WHERE student_id=%s AND branch_id=%s ORDER BY date DESC", (student_id, branch_id), all_rows=True)


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def generate_timetable(cfg):
    unavailable = set(cfg.get("unavailable_days", []))
    days = [d for d in cfg["days"] if d not in unavailable]
    slots = cfg["slots"]
    if not days or not slots:
        raise HTTPException(400, "At least one available day and time slot are required")
    requested = sum(int(s.get("lectures_per_week", 0)) for s in cfg["subjects"])
    if requested != int(cfg.get("lectures_per_week", requested)):
        raise HTTPException(400, "Total lectures per week must equal the sum of subject lecture requirements")
    if requested > len(days) * len(slots):
        raise HTTPException(400, "Lectures per week exceed available timetable capacity")
    teacher_set = cfg.get("teachers", []); room_set = cfg.get("rooms", []); out=[]; idx=0; gen=secrets.token_hex(8)
    counts = {s["name"]: int(s.get("lectures_per_week", 0)) for s in cfg["subjects"]}
    names = list(counts)
    for d in days:
        used=set()
        for t in slots:
            choices=[n for n in names if counts[n]>0 and n not in used] or [n for n in names if counts[n]>0]
            if not choices: break
            subject=sorted(choices,key=lambda n:counts[n],reverse=True)[0]
            counts[subject]-=1; used.add(subject); idx+=1
            s=next(z for z in cfg["subjects"] if z["name"]==subject)
            teacher=s.get("teacher") or (teacher_set[(idx-1)%len(teacher_set)] if teacher_set else "")
            room=s.get("room") or (room_set[(idx-1)%len(room_set)] if room_set else "")
            out.append((d,t,idx,subject,teacher,room,gen))
    if any(v for v in counts.values()):
        raise HTTPException(400, "Could not satisfy all lectures-per-week prerequisites")
    return gen,out


@app.get("/api/timetable")
def timetable(branch_id: int, batch_name: str, c=Depends(current)):
    check_access("timetable", c); branch_ok(branch_id, c["id"])
    return q("SELECT * FROM timetable_slots WHERE branch_id=%s AND batch_name=%s ORDER BY array_position(%s::text[],day),lecture_number", (branch_id, batch_name, DAYS), all_rows=True)


@app.get("/api/timetable/config")
def timetable_config(branch_id: int, batch_name: str, c=Depends(current)):
    check_access("timetable", c); branch_ok(branch_id, c["id"])
    r=q("SELECT config FROM timetable_configs WHERE branch_id=%s AND batch_name=%s", (branch_id,batch_name), one=True)
    return r["config"] if r else None


@app.post("/api/timetable/generate")
def gen_timetable(x: TimetableIn, branch_id: int, c=Depends(current)):
    check_access("timetable", c, True); branch_ok(branch_id, c["id"])
    cfg=x.model_dump(); gen,slots=generate_timetable(cfg)
    with db() as conn:
        # One transaction: the selected batch is cleared and replaced atomically.
        # A second generation therefore cannot stack on top of the first one.
        conn.execute("DELETE FROM timetable_slots WHERE branch_id=%s AND batch_name=%s", (branch_id,x.batch_name))
        conn.execute("INSERT INTO timetable_configs(branch_id,batch_name,config,updated_at) VALUES(%s,%s,%s,now()) ON CONFLICT(branch_id,batch_name) DO UPDATE SET config=EXCLUDED.config,updated_at=now()", (branch_id,x.batch_name,Jsonb(cfg)))
        for s in slots:
            conn.execute("INSERT INTO timetable_slots(branch_id,batch_name,day,time_slot,lecture_number,subject,teacher,room,generation_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", (branch_id,x.batch_name,*s))
        conn.commit()
    return {"generation_id":gen,"slots":slots,"replaced_previous":True}


@app.patch("/api/timetable/{rid}")
def edit_timetable(rid: int, x: TimetableEdit, branch_id: int, c=Depends(current)):
    check_access("timetable", c, True); branch_ok(branch_id, c["id"])
    q("UPDATE timetable_slots SET day=%s,time_slot=%s,lecture_number=%s,subject=%s,teacher=%s,room=%s WHERE id=%s AND branch_id=%s", (x.day,x.time_slot,x.lecture_number,x.subject,x.teacher,x.room,rid,branch_id))
    return {"ok":True}


@app.get("/api/dashboard")
def dashboard(branch_id: int, c=Depends(current)):
    branch_ok(branch_id,c["id"])
    today=date.today(); monday=today-timedelta(days=today.weekday()); sunday=monday+timedelta(days=6)
    att=q("SELECT batch,COUNT(*) FILTER(WHERE status ILIKE 'present') present,COUNT(*) total FROM attendance WHERE branch_id=%s AND date BETWEEN %s AND %s GROUP BY batch ORDER BY batch",(branch_id,monday,sunday),all_rows=True)
    fees=q("SELECT COALESCE(SUM(amount_inr),0) pending,COUNT(*) count FROM fees WHERE branch_id=%s AND status NOT ILIKE 'paid'",(branch_id,),one=True)
    now=datetime.now(IST); day=now.strftime("%A"); hm=now.strftime("%H:%M")
    current_slots=q("SELECT batch_name,lecture_number,time_slot,subject,teacher,room FROM timetable_slots WHERE branch_id=%s AND day=%s",(branch_id,day),all_rows=True)
    ongoing=[]
    for r in current_slots:
        try:
            a,b=[z.strip() for z in r["time_slot"].split("-",1)]
            if a<=hm<=b: ongoing.append(r)
        except Exception: pass
    batches=q("SELECT batch,COUNT(*) students FROM students WHERE branch_id=%s GROUP BY batch ORDER BY batch",(branch_id,),all_rows=True)
    return {"attendance_week":att,"fees_pending":fees,"ongoing_lectures":ongoing,"students_by_batch":batches,"today":today.isoformat(),"now":now.isoformat()}


@app.get("/api/export/{module}")
def export_module(module: str, branch_id: int, format: str="xlsx", c=Depends(current)):
    if module not in TABLES: raise HTTPException(404,"Unknown module")
    check_access(module,c); branch_ok(branch_id,c["id"])
    table,cols=TABLES[module]; rows=q(f"SELECT {','.join(cols)} FROM {table} WHERE branch_id=%s ORDER BY id",(branch_id,),all_rows=True)
    headers=cols; values=[[r.get(k) for k in cols] for r in rows]
    if format.lower()=="xlsx":
        wb=Workbook(); ws=wb.active; ws.title=module.title(); ws.append(headers)
        for row in values: ws.append(row)
        for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=min(32,max(12,max((len(str(x.value)) for x in col if x.value is not None),default=0)+2))
        bio=BytesIO(); wb.save(bio); bio.seek(0)
        return StreamingResponse(bio,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{module}.xlsx"'})
    if format.lower()=="pdf":
        bio=BytesIO(); doc=SimpleDocTemplate(bio,pagesize=landscape(A4),rightMargin=20,leftMargin=20,topMargin=20,bottomMargin=20); styles=getSampleStyleSheet(); story=[Paragraph(module.title(),styles["Title"]),Spacer(1,12)]
        data=[headers]+[[str(v or "") for v in row] for row in values]; t=Table(data,repeatRows=1); t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#222222")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),0.25,colors.grey),("FONTSIZE",(0,0),(-1,-1),7),("VALIGN",(0,0),(-1,-1),"MIDDLE")])) ; story.append(t); doc.build(story); bio.seek(0)
        return StreamingResponse(bio,media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{module}.pdf"'})
    raise HTTPException(400,"format must be xlsx or pdf")


@app.get("/")
def root():
    try:
        with open("index.html",encoding="utf-8") as f: return HTMLResponse(f.read())
    except FileNotFoundError: return HTMLResponse("ALGORITHMIC backend is running")
