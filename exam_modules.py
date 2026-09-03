import json
from datetime import datetime
from fastapi import Depends, HTTPException
from pydantic import BaseModel
import main

# New examination data tables are created additively so existing production data is preserved.
def _init_exam_tables():
    conn = main.get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS exam_results (
            id SERIAL PRIMARY KEY,
            branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
            batch_name TEXT NOT NULL,
            subjects TEXT NOT NULL,
            topics TEXT NOT NULL,
            exam_date TEXT NOT NULL,
            overall_marks NUMERIC(12,2) NOT NULL,
            student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
            student_name TEXT NOT NULL,
            marks NUMERIC(12,2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(branch_id, batch_name, subjects, topics, exam_date, student_id)
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS exam_history (
            id SERIAL PRIMARY KEY,
            branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            topic TEXT NOT NULL,
            batch_name TEXT NOT NULL,
            exam_date TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_results_branch_batch ON exam_results(branch_id, batch_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_history_branch_date ON exam_history(branch_id, exam_date DESC)")
        conn.commit()
    finally:
        conn.close()

_init_exam_tables()


def _check(institute, module='results'):
    main.check_module_access(institute, module)

class ResultGenerateRequest(BaseModel):
    branch_id: int
    batch_name: str
    subjects: str
    topics: str
    exam_date: str
    overall_marks: float

class ResultMarkUpdate(BaseModel):
    marks: float | None = None

class ResultRecordUpdate(BaseModel):
    subjects: str
    topics: str
    exam_date: str
    overall_marks: float
    marks: float | None = None

class HistoryCreateRequest(BaseModel):
    branch_id: int
    subject: str
    topic: str
    batch_name: str
    exam_date: str

class HistoryUpdateRequest(BaseModel):
    subject: str
    topic: str
    batch_name: str
    exam_date: str

@app.get('/api/exams/results/{branch_id}')
def get_results(branch_id: int, batch_name: str = '', institute=Depends(main.get_current_institute)):
    _check(institute, 'results')
    main.verify_branch_read_access(branch_id, institute.id)
    conn = main.get_conn()
    try:
        cur = conn.cursor()
        scope = 'branch_id IN (SELECT id FROM branches WHERE tenant_id = %s)' if branch_id == 0 else 'branch_id = %s'
        params = [institute.id if branch_id == 0 else branch_id]
        where = scope
        if batch_name.strip():
            where += ' AND batch_name = %s'
            params.append(batch_name.strip())
        cur.execute(f'''SELECT * FROM exam_results WHERE {where} ORDER BY exam_date DESC, batch_name, student_name, id''', params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.get('/api/exams/result-batches/{branch_id}')
def get_result_batches(branch_id: int, institute=Depends(main.get_current_institute)):
    _check(institute, 'results')
    main.verify_branch_read_access(branch_id, institute.id)
    conn = main.get_conn()
    try:
        cur = conn.cursor()
        if branch_id == 0:
            cur.execute('SELECT DISTINCT batch FROM students WHERE branch_id IN (SELECT id FROM branches WHERE tenant_id = %s) AND COALESCE(batch, \'\') <> \'\' ORDER BY batch', (institute.id,))
        else:
            cur.execute('SELECT DISTINCT batch FROM students WHERE branch_id = %s AND COALESCE(batch, \'\') <> \'\' ORDER BY batch', (branch_id,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

@app.post('/api/exams/results/generate')
def generate_results(req: ResultGenerateRequest, institute=Depends(main.require_write_access)):
    _check(institute, 'results')
    main.verify_branch_ownership(req.branch_id, institute.id)
    batch = req.batch_name.strip(); subjects = req.subjects.strip(); topics = req.topics.strip(); date = req.exam_date.strip()
    if not batch or not subjects or not topics or not date or req.overall_marks <= 0:
        raise HTTPException(status_code=400, detail='Batch, subject(s), topic(s), exam date and a positive overall marks value are required.')
    conn = main.get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id, name FROM students WHERE branch_id = %s AND batch = %s ORDER BY LOWER(name)', (req.branch_id, batch))
        students = cur.fetchall()
        if not students:
            raise HTTPException(status_code=400, detail=f'No students found in batch "{batch}".')
        # Re-running the exact paper replaces its student rows, preventing duplicates.
        cur.execute('DELETE FROM exam_results WHERE branch_id=%s AND batch_name=%s AND subjects=%s AND topics=%s AND exam_date=%s', (req.branch_id, batch, subjects, topics, date))
        for s in students:
            cur.execute('''INSERT INTO exam_results (branch_id,batch_name,subjects,topics,exam_date,overall_marks,student_id,student_name,marks)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL)''', (req.branch_id,batch,subjects,topics,date,req.overall_marks,s['id'],s['name']))
        conn.commit()
        cur.execute('''SELECT * FROM exam_results WHERE branch_id=%s AND batch_name=%s AND subjects=%s AND topics=%s AND exam_date=%s ORDER BY LOWER(student_name)''', (req.branch_id,batch,subjects,topics,date))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.patch('/api/exams/results/{record_id}')
def update_result(record_id: int, req: ResultRecordUpdate, institute=Depends(main.require_write_access)):
    _check(institute, 'results')
    conn = main.get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''SELECT exam_results.* FROM exam_results JOIN branches ON branches.id=exam_results.branch_id
                       WHERE exam_results.id=%s AND branches.tenant_id=%s''', (record_id,institute.id))
        before = cur.fetchone()
        if not before: raise HTTPException(status_code=404, detail='Result record not found')
        if req.overall_marks <= 0 or req.marks is not None and (req.marks < 0 or req.marks > req.overall_marks):
            raise HTTPException(status_code=400, detail='Marks must be between 0 and the overall marks.')
        cur.execute('''UPDATE exam_results SET subjects=%s, topics=%s, exam_date=%s, overall_marks=%s, marks=%s WHERE id=%s''',
                    (req.subjects.strip(),req.topics.strip(),req.exam_date.strip(),req.overall_marks,req.marks,record_id))
        conn.commit()
        return {'status':'updated'}
    finally: conn.close()

@app.delete('/api/exams/results/{record_id}')
def delete_result(record_id: int, institute=Depends(main.require_write_access)):
    _check(institute, 'results')
    conn = main.get_conn()
    try:
        cur=conn.cursor(); cur.execute('''SELECT exam_results.* FROM exam_results JOIN branches ON branches.id=exam_results.branch_id WHERE exam_results.id=%s AND branches.tenant_id=%s''',(record_id,institute.id)); before=cur.fetchone()
        if not before: raise HTTPException(status_code=404, detail='Result record not found')
        cur.execute('DELETE FROM exam_results WHERE id=%s',(record_id,)); conn.commit(); return {'status':'deleted'}
    finally: conn.close()

@app.get('/api/exams/history/{branch_id}')
def get_exam_history(branch_id:int, institute=Depends(main.get_current_institute)):
    _check(institute, 'history')
    main.verify_branch_read_access(branch_id,institute.id)
    conn=main.get_conn()
    try:
        cur=conn.cursor(); scope='branch_id IN (SELECT id FROM branches WHERE tenant_id=%s)' if branch_id==0 else 'branch_id=%s'; param=institute.id if branch_id==0 else branch_id
        cur.execute(f'SELECT * FROM exam_history WHERE {scope} ORDER BY exam_date DESC,id DESC',(param,)); return [dict(r) for r in cur.fetchall()]
    finally: conn.close()

@app.post('/api/exams/history')
def create_exam_history(req:HistoryCreateRequest,institute=Depends(main.require_write_access)):
    _check(institute,'history'); main.verify_branch_ownership(req.branch_id,institute.id)
    vals=[x.strip() for x in (req.subject,req.topic,req.batch_name,req.exam_date)]
    if not all(vals): raise HTTPException(status_code=400,detail='Subject, topic, batch name and exam date are required.')
    conn=main.get_conn()
    try:
        cur=conn.cursor(); cur.execute('INSERT INTO exam_history(branch_id,subject,topic,batch_name,exam_date) VALUES(%s,%s,%s,%s,%s) RETURNING id',(req.branch_id,*vals)); row=cur.fetchone(); conn.commit(); return {'id':row[0], 'status':'created'}
    finally: conn.close()

@app.patch('/api/exams/history/{record_id}')
def update_exam_history(record_id:int,req:HistoryUpdateRequest,institute=Depends(main.require_write_access)):
    _check(institute,'history'); vals=[x.strip() for x in (req.subject,req.topic,req.batch_name,req.exam_date)]
    if not all(vals): raise HTTPException(status_code=400,detail='All history fields are required.')
    conn=main.get_conn()
    try:
        cur=conn.cursor(); cur.execute('''UPDATE exam_history SET subject=%s,topic=%s,batch_name=%s,exam_date=%s WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE tenant_id=%s)''',(*vals,record_id,institute.id));
        if cur.rowcount==0: raise HTTPException(status_code=404,detail='Exam history record not found')
        conn.commit(); return {'status':'updated'}
    finally: conn.close()

@app.delete('/api/exams/history/{record_id}')
def delete_exam_history(record_id:int,institute=Depends(main.require_write_access)):
    _check(institute,'history'); conn=main.get_conn()
    try:
        cur=conn.cursor(); cur.execute('DELETE FROM exam_history WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE tenant_id=%s)',(record_id,institute.id));
        if cur.rowcount==0: raise HTTPException(status_code=404,detail='Exam history record not found')
        conn.commit(); return {'status':'deleted'}
    finally: conn.close()

# Register routes by importing this module from the production entrypoint.
