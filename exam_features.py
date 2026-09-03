import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import main

router = APIRouter(prefix="/api/examination", tags=["examination"])

def db(): return main.get_conn()

def access(institute, branch_id):
    main.check_module_access(institute, "seating")
    main.verify_branch_read_access(branch_id, institute.id)

def write_access(institute, branch_id):
    main.check_module_access(institute, "seating")
    if institute.permission == "read_only": raise HTTPException(status_code=403, detail="Read-only accounts cannot modify examination records")
    main.verify_branch_ownership(branch_id, institute.id)

def ensure_tables():
    conn=db(); cur=conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS exam_history (id SERIAL PRIMARY KEY, branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE, subject TEXT NOT NULL, topic TEXT NOT NULL, batch_name TEXT NOT NULL, exam_date TEXT NOT NULL, overall_marks NUMERIC(12,2) NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    cur.execute("""CREATE TABLE IF NOT EXISTS exam_results (id SERIAL PRIMARY KEY, history_id INTEGER NOT NULL REFERENCES exam_history(id) ON DELETE CASCADE, branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE, student_id INTEGER REFERENCES students(id) ON DELETE SET NULL, student_name TEXT NOT NULL, roll_number TEXT, marks NUMERIC(12,2), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_history_branch ON exam_history(branch_id, exam_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_results_history ON exam_results(history_id)")
    conn.commit(); cur.close(); conn.close()

ensure_tables()

@router.get("/results/{branch_id}")
def list_results(branch_id:int,institute=Depends(main.get_current_institute)):
    access(institute,branch_id); conn=db(); cur=conn.cursor(); cur.execute("SELECT id,subject,topic,batch_name,exam_date,overall_marks FROM exam_history WHERE branch_id=%s ORDER BY exam_date DESC,id DESC",(branch_id,)); exams=[dict(r) for r in cur.fetchall()]
    for e in exams:
        cur.execute("SELECT id,student_id,student_name,roll_number,marks FROM exam_results WHERE history_id=%s ORDER BY LOWER(student_name)",(e['id'],)); e['students']=[dict(r) for r in cur.fetchall()]
    conn.close(); return exams

@router.post("/results/{branch_id}")
def create_result_exam(branch_id:int,payload:dict,institute=Depends(main.get_current_institute)):
    write_access(institute,branch_id); subject=str(payload.get('subject','')).strip(); topic=str(payload.get('topic','')).strip(); batch=str(payload.get('batch_name','')).strip(); exam_date=str(payload.get('exam_date','')).strip()
    try: overall=float(payload.get('overall_marks'))
    except (TypeError,ValueError): raise HTTPException(status_code=400,detail='Overall marks must be a number')
    if not all([subject,topic,batch,exam_date]) or overall<=0: raise HTTPException(status_code=400,detail='Batch, subject, topic, date and positive overall marks are required')
    conn=db(); cur=conn.cursor(); cur.execute("SELECT id,name,roll_number FROM students WHERE branch_id=%s AND COALESCE(batch,'')=%s ORDER BY LOWER(name)",(branch_id,batch)); students=cur.fetchall()
    if not students: conn.close(); raise HTTPException(status_code=400,detail=f"No students found in batch '{batch}'")
    cur.execute("INSERT INTO exam_history(branch_id,subject,topic,batch_name,exam_date,overall_marks) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",(branch_id,subject,topic,batch,exam_date,overall)); history_id=cur.fetchone()[0]
    for s in students: cur.execute("INSERT INTO exam_results(history_id,branch_id,student_id,student_name,roll_number) VALUES(%s,%s,%s,%s,%s)",(history_id,branch_id,s['id'],s['name'] or '',s['roll_number'] or ''))
    conn.commit(); conn.close(); return {'id':history_id}

@router.patch("/results/records/{result_id}")
def update_result(result_id:int,payload:dict,institute=Depends(main.get_current_institute)):
    conn=db(); cur=conn.cursor(); cur.execute("SELECT * FROM exam_results WHERE id=%s",(result_id,)); row=cur.fetchone()
    if not row: conn.close(); raise HTTPException(status_code=404,detail='Result record not found')
    write_access(institute,row['branch_id']); value=payload.get('marks'); marks=None if value in (None,'') else float(value); cur.execute("SELECT overall_marks FROM exam_history WHERE id=%s",(row['history_id'],)); maximum=float(cur.fetchone()[0])
    if marks is not None and (marks<0 or marks>maximum): conn.close(); raise HTTPException(status_code=400,detail=f'Marks must be between 0 and {maximum:g}')
    cur.execute("UPDATE exam_results SET marks=%s,updated_at=NOW() WHERE id=%s",(marks,result_id)); conn.commit(); conn.close(); return {'ok':True}

@router.delete("/results/records/{result_id}")
def delete_result(result_id:int,institute=Depends(main.get_current_institute)):
    conn=db(); cur=conn.cursor(); cur.execute("SELECT branch_id FROM exam_results WHERE id=%s",(result_id,)); row=cur.fetchone()
    if not row: conn.close(); raise HTTPException(status_code=404,detail='Result record not found')
    write_access(institute,row['branch_id']); cur.execute("DELETE FROM exam_results WHERE id=%s",(result_id,)); conn.commit(); conn.close(); return {'ok':True}

@router.get("/history/{branch_id}")
def list_history(branch_id:int,institute=Depends(main.get_current_institute)):
    access(institute,branch_id); conn=db(); cur=conn.cursor(); cur.execute("SELECT id,subject,topic,batch_name,exam_date,overall_marks FROM exam_history WHERE branch_id=%s ORDER BY exam_date DESC,id DESC",(branch_id,)); rows=[dict(r) for r in cur.fetchall()]; conn.close(); return rows

@router.post("/history/{branch_id}")
def create_history(branch_id:int,payload:dict,institute=Depends(main.get_current_institute)):
    write_access(institute,branch_id); vals=[str(payload.get(k,'')).strip() for k in ('subject','topic','batch_name','exam_date')]
    if not all(vals): raise HTTPException(status_code=400,detail='Subject, topic, batch and date are required')
    conn=db(); cur=conn.cursor(); cur.execute("INSERT INTO exam_history(branch_id,subject,topic,batch_name,exam_date,overall_marks) VALUES(%s,%s,%s,%s,%s,1) RETURNING id",(branch_id,*vals)); rid=cur.fetchone()[0]; conn.commit(); conn.close(); return {'id':rid}

@router.patch("/history/{history_id}")
def update_history(history_id:int,payload:dict,institute=Depends(main.get_current_institute)):
    conn=db(); cur=conn.cursor(); cur.execute("SELECT * FROM exam_history WHERE id=%s",(history_id,)); row=cur.fetchone()
    if not row: conn.close(); raise HTTPException(status_code=404,detail='History record not found')
    write_access(institute,row['branch_id']); fields={k:str(payload[k]).strip() for k in ('subject','topic','batch_name','exam_date') if k in payload};
    if 'overall_marks' in payload: fields['overall_marks']=float(payload['overall_marks'])
    if fields:
        sql=', '.join(f'{k}=%s' for k in fields); cur.execute(f'UPDATE exam_history SET {sql},updated_at=NOW() WHERE id=%s',(*fields.values(),history_id))
    conn.commit(); conn.close(); return {'ok':True}

@router.delete("/history/{history_id}")
def delete_history(history_id:int,institute=Depends(main.get_current_institute)):
    conn=db(); cur=conn.cursor(); cur.execute("SELECT branch_id FROM exam_history WHERE id=%s",(history_id,)); row=cur.fetchone()
    if not row: conn.close(); raise HTTPException(status_code=404,detail='History record not found')
    write_access(institute,row['branch_id']); cur.execute("DELETE FROM exam_history WHERE id=%s",(history_id,)); conn.commit(); conn.close(); return {'ok':True}
