from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main_path = ROOT / 'main.py'
index_path = ROOT / 'index.html'

MAIN_MARKER = '# === FINAL EXAM MODULES V2 ==='
INDEX_MARKER = '<!-- === FINAL EXAM MODULES V2 === -->'

main = main_path.read_text(encoding='utf-8')
index = index_path.read_text(encoding='utf-8')

if MAIN_MARKER not in main:
    main += r'''

# === FINAL EXAM MODULES V2 ===
# Stable, isolated API for the Results and History screens.  These endpoints
# intentionally use separate table names so they cannot conflict with older
# experimental exam-module migrations.

def _init_final_exam_tables():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS exam_results_v2 (
        id SERIAL PRIMARY KEY,
        branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
        batch_name TEXT NOT NULL,
        subjects TEXT NOT NULL,
        topics TEXT NOT NULL,
        exam_date TEXT NOT NULL,
        overall_marks NUMERIC(12,2) NOT NULL,
        student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
        student_name TEXT NOT NULL,
        roll_number TEXT,
        marks NUMERIC(12,2),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS exam_history_v2 (
        id SERIAL PRIMARY KEY,
        branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
        subject TEXT NOT NULL,
        topic TEXT NOT NULL,
        batch_name TEXT NOT NULL,
        exam_date TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_results_v2_branch_batch ON exam_results_v2(branch_id,batch_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_history_v2_branch_date ON exam_history_v2(branch_id,exam_date)")
    conn.commit()
    conn.close()

_init_final_exam_tables()

class FinalExamResultStudent(BaseModel):
    student_id: int | None = None
    student_name: str
    roll_number: str | None = None
    marks: float | None = None

class FinalExamResultsCreate(BaseModel):
    branch_id: int
    batch_name: str
    subjects: str
    topics: str
    exam_date: str
    overall_marks: float
    students: list[FinalExamResultStudent]

class FinalExamResultUpdate(BaseModel):
    marks: float | None = None
    student_name: str | None = None
    roll_number: str | None = None

class FinalExamHistoryCreate(BaseModel):
    branch_id: int
    subject: str
    topic: str
    batch_name: str
    exam_date: str

class FinalExamHistoryUpdate(BaseModel):
    subject: str
    topic: str
    batch_name: str
    exam_date: str

def _final_exam_access(institute, branch_id, write=False):
    check_module_access(institute, 'examination')
    if write:
        if not institute.is_owner and institute.permission != 'edit':
            raise HTTPException(status_code=403, detail='Edit access is required')
        verify_branch_ownership(branch_id, institute.id)
    else:
        verify_branch_read_access(branch_id, institute.id)

def _final_mark(value, overall):
    if value is None:
        return None
    value = float(value)
    if value < 0 or value > overall:
        raise HTTPException(status_code=400, detail=f'Marks must be between 0 and {overall:g}')
    return value

@app.get('/api/examination/results/students/{branch_id}')
def final_exam_students(branch_id: int, batch: str, institute: CurrentInstitute = Depends(get_current_institute)):
    _final_exam_access(institute, branch_id)
    if not batch.strip():
        raise HTTPException(status_code=400, detail='Batch name is required')
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id,name,COALESCE(roll_number,'') AS roll_number
                       FROM students
                       WHERE branch_id=%s AND LOWER(TRIM(COALESCE(batch,'')))=LOWER(TRIM(%s))
                       ORDER BY LOWER(COALESCE(name,'')),LOWER(COALESCE(roll_number,''))""", (branch_id,batch))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.get('/api/examination/results/{branch_id}')
def final_exam_results(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    _final_exam_access(institute, branch_id)
    conn=get_conn()
    try:
        cur=conn.cursor()
        cur.execute("""SELECT id,batch_name,subjects,topics,exam_date,overall_marks,
                              student_id,student_name,roll_number,marks,created_at,updated_at
                       FROM exam_results_v2 WHERE branch_id=%s
                       ORDER BY exam_date DESC,batch_name,student_name,id""",(branch_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.post('/api/examination/results')
def final_create_exam_results(payload: FinalExamResultsCreate, institute: CurrentInstitute = Depends(require_write_access)):
    _final_exam_access(institute,payload.branch_id,write=True)
    if not payload.batch_name.strip() or not payload.subjects.strip() or not payload.topics.strip() or not payload.exam_date.strip():
        raise HTTPException(status_code=400,detail='Batch, subject(s), topic(s), and exam date are required')
    overall=float(payload.overall_marks)
    if overall<=0: raise HTTPException(status_code=400,detail='Overall marks must be greater than zero')
    if not payload.students: raise HTTPException(status_code=400,detail='No students were supplied')
    conn=get_conn(); ids=[]
    try:
        cur=conn.cursor()
        for s in payload.students:
            cur.execute("""INSERT INTO exam_results_v2
                (branch_id,batch_name,subjects,topics,exam_date,overall_marks,student_id,student_name,roll_number,marks)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (payload.branch_id,payload.batch_name.strip(),payload.subjects.strip(),payload.topics.strip(),payload.exam_date,
                 overall,s.student_id,s.student_name.strip(),s.roll_number or '',_final_mark(s.marks,overall)))
            ids.append(cur.fetchone()[0])
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    return {'status':'success','ids':ids}

@app.patch('/api/examination/results/{result_id}')
def final_update_exam_result(result_id:int,payload:FinalExamResultUpdate,institute:CurrentInstitute=Depends(require_write_access)):
    check_module_access(institute,'examination')
    conn=get_conn()
    try:
        cur=conn.cursor()
        cur.execute("""SELECT r.* FROM exam_results_v2 r JOIN branches b ON b.id=r.branch_id
                       WHERE r.id=%s AND b.tenant_id=%s""",(result_id,institute.id))
        row=cur.fetchone()
        if not row: raise HTTPException(status_code=404,detail='Result record not found')
        if payload.marks is not None: _final_mark(payload.marks,float(row['overall_marks']))
        sets=[]; vals=[]
        if payload.marks is not None: sets.append('marks=%s'); vals.append(payload.marks)
        if payload.student_name is not None: sets.append('student_name=%s'); vals.append(payload.student_name.strip())
        if payload.roll_number is not None: sets.append('roll_number=%s'); vals.append(payload.roll_number.strip())
        if not sets: raise HTTPException(status_code=400,detail='Nothing to update')
        sets.append('updated_at=NOW()')
        cur.execute(f"UPDATE exam_results_v2 SET {','.join(sets)} WHERE id=%s",(*vals,result_id))
        conn.commit(); return {'status':'updated','id':result_id}
    finally:
        conn.close()

@app.delete('/api/examination/results/{result_id}')
def final_delete_exam_result(result_id:int,institute:CurrentInstitute=Depends(require_write_access)):
    check_module_access(institute,'examination')
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("DELETE FROM exam_results_v2 WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE tenant_id=%s) RETURNING id",(result_id,institute.id))
        if not cur.fetchone(): raise HTTPException(status_code=404,detail='Result record not found')
        conn.commit(); return {'status':'deleted'}
    finally: conn.close()

@app.get('/api/examination/history/{branch_id}')
def final_exam_history(branch_id:int,institute:CurrentInstitute=Depends(get_current_institute)):
    _final_exam_access(institute,branch_id)
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("SELECT id,subject,topic,batch_name,exam_date,created_at,updated_at FROM exam_history_v2 WHERE branch_id=%s ORDER BY exam_date DESC,id DESC",(branch_id,))
        return [dict(r) for r in cur.fetchall()]
    finally: conn.close()

@app.post('/api/examination/history')
def final_create_exam_history(payload:FinalExamHistoryCreate,institute:CurrentInstitute=Depends(require_write_access)):
    _final_exam_access(institute,payload.branch_id,write=True)
    if not all(v.strip() for v in (payload.subject,payload.topic,payload.batch_name,payload.exam_date)):
        raise HTTPException(status_code=400,detail='All history fields are required')
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("INSERT INTO exam_history_v2(branch_id,subject,topic,batch_name,exam_date) VALUES(%s,%s,%s,%s,%s) RETURNING id",(payload.branch_id,payload.subject.strip(),payload.topic.strip(),payload.batch_name.strip(),payload.exam_date)); rid=cur.fetchone()[0]; conn.commit(); return {'status':'success','id':rid}
    finally: conn.close()

@app.patch('/api/examination/history/{history_id}')
def final_update_exam_history(history_id:int,payload:FinalExamHistoryUpdate,institute:CurrentInstitute=Depends(require_write_access)):
    check_module_access(institute,'examination')
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("UPDATE exam_history_v2 SET subject=%s,topic=%s,batch_name=%s,exam_date=%s,updated_at=NOW() WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE tenant_id=%s) RETURNING id",(payload.subject.strip(),payload.topic.strip(),payload.batch_name.strip(),payload.exam_date,history_id,institute.id));
        if not cur.fetchone(): raise HTTPException(status_code=404,detail='History record not found')
        conn.commit(); return {'status':'updated','id':history_id}
    finally: conn.close()

@app.delete('/api/examination/history/{history_id}')
def final_delete_exam_history(history_id:int,institute:CurrentInstitute=Depends(require_write_access)):
    check_module_access(institute,'examination')
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("DELETE FROM exam_history_v2 WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE tenant_id=%s) RETURNING id",(history_id,institute.id))
        if not cur.fetchone(): raise HTTPException(status_code=404,detail='History record not found')
        conn.commit(); return {'status':'deleted'}
    finally: conn.close()
'''
    main_path.write_text(main,encoding='utf-8')

if INDEX_MARKER not in index:
    frontend = r'''

<!-- === FINAL EXAM MODULES V2 === -->
<style>
#examV2Root input{box-sizing:border-box}.examv2-card{background:#0b0b0b;border:1px solid rgba(212,175,55,.2);border-radius:16px;padding:20px}.examv2-input{width:100%;background:#050505;border:1px solid #292929;border-radius:10px;color:#eee;padding:10px 12px;font-size:12px;outline:none}.examv2-input:focus{border-color:#a88428}.examv2-btn{background:#111;border:1px solid rgba(212,175,55,.28);color:#e8c767;border-radius:9px;padding:9px 13px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}.examv2-btn.primary{background:linear-gradient(135deg,#eacd6e,#aa771c);color:#080808}.examv2-btn.danger{color:#f87171;border-color:rgba(248,113,113,.25)}.examv2-table{width:100%;border-collapse:collapse}.examv2-table th{padding:11px;text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#777;border-bottom:1px solid #242424}.examv2-table td{padding:9px 11px;border-bottom:1px solid #181818;font-size:12px}.examv2-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}@media(max-width:900px){.examv2-grid{grid-template-columns:1fr 1fr}}@media(max-width:600px){.examv2-grid{grid-template-columns:1fr}}
</style>
<script>
(function(){
  const api=async(url,opt={})=>{const r=await authFetch(url,opt);const t=await r.text();let d={};try{d=t?JSON.parse(t):{}}catch(e){}if(!r.ok)throw new Error(d.detail||`Request failed (${r.status})`);return d};
  const bid=()=>Number(currentBranchId);
  const escv=v=>typeof esc==='function'?esc(v??''):String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function host(){return document.getElementById('mainContent')||document.querySelector('main')}
  function addNav(){
    document.querySelectorAll('button').forEach(b=>{if(/Exam Seating/i.test(b.textContent||'')){b.textContent=(b.textContent||'').replace(/Exam Seating/gi,'Seating')}if(/Exam Invigilation/i.test(b.textContent||'')){b.textContent=(b.textContent||'').replace(/Exam Invigilation/gi,'Invigilation')}});
    const group=document.querySelector('.alg-head-group[data-head="examination"]'); const items=group?.querySelector('.alg-head-items'); if(!items)return;
    if(!items.querySelector('[data-exam-v2="results"]')){const b=document.createElement('button');b.dataset.examV2='results';b.className='sidebar-item alg-child w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3';b.innerHTML='<span>▣</span><span>Results</span>';b.onclick=openResults;items.appendChild(b)}
    if(!items.querySelector('[data-exam-v2="history"]')){const b=document.createElement('button');b.dataset.examV2='history';b.className='sidebar-item alg-child w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3';b.innerHTML='<span>◷</span><span>History</span>';b.onclick=openHistory;items.appendChild(b)}
  }
  new MutationObserver(addNav).observe(document.body,{subtree:true,childList:true});setTimeout(addNav,100);setTimeout(addNav,1000);
  function shell(title,sub){const h=host();h.innerHTML=`<div id="examV2Root" class="space-y-6"><div><div class="text-[10px] uppercase tracking-[.2em] text-yellow-600 font-bold">Examination</div><h2 class="command-heading-font text-4xl gold-gradient-text mt-1">${title}</h2><p class="text-sm text-gray-500 mt-2">${sub}</p></div></div>`;return document.getElementById('examV2Root')}
  const f=(id,l,t='text',ph='')=>`<label class="block"><span class="block text-[10px] uppercase tracking-widest text-gray-500 font-bold mb-2">${l}</span><input id="${id}" type="${t}" placeholder="${ph}" class="examv2-input"></label>`;
  window.openResults=async function(){
    if(!isOwner&&!myAllowedModules.includes('examination'))return alert('You do not have Examination access.');
    const root=shell('Results','Enter the paper details, then load the selected batch and enter each student’s marks.');
    root.insertAdjacentHTML('beforeend',`<div class="examv2-card space-y-5"><div class="examv2-grid">${f('v2Batch','Batch name','text','Batch A')}${f('v2Subjects','Subject(s)','text','Physics, Chemistry')}${f('v2Topics','Topic(s)','text','Kinematics')}${f('v2Date','Exam date','date')}${f('v2Overall','Overall marks','number','100')}</div><div class="flex flex-wrap gap-2"><button id="v2Load" class="examv2-btn primary">Load Students</button><button id="v2Save" class="examv2-btn primary">Save Results</button><button id="v2Pdf" class="examv2-btn">Download PDF</button></div><div id="v2Meta" class="hidden text-xs text-gray-400 bg-[#070707] border border-gray-900 rounded-xl p-4"></div><div id="v2Students"></div></div><div class="examv2-card"><div class="flex justify-between items-center mb-4"><h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Saved Results</h3><span class="text-xs text-gray-600">Editable and deletable</span></div><div id="v2Saved"></div></div>`);
    v2Load.onclick=loadStudents;v2Save.onclick=saveResults;v2Pdf.onclick=pdfResults;await loadSavedResults();
  };
  async function loadStudents(){try{const batch=v2Batch.value.trim(),overall=Number(v2Overall.value);if(!batch||overall<=0)throw Error('Enter a batch and valid overall marks.');const s=await api(`/api/examination/results/students/${bid()}?batch=${encodeURIComponent(batch)}`);window.__v2students=s;v2Meta.classList.remove('hidden');v2Meta.innerHTML=`Batch: <b>${escv(batch)}</b> · Subject(s): <b>${escv(v2Subjects.value)}</b> · Topic(s): <b>${escv(v2Topics.value)}</b> · Date: <b>${escv(v2Date.value)}</b>`;v2Students.innerHTML=(s||[]).length?`<div class="overflow-x-auto"><table class="examv2-table"><thead><tr><th>Student</th><th>Roll Number</th><th>Total / ${overall}</th></tr></thead><tbody>${s.map(x=>`<tr><td class="text-white font-semibold">${escv(x.name)}</td><td class="text-gray-500">${escv(x.roll_number)}</td><td><input data-v2-mark="${x.id}" type="number" min="0" max="${overall}" step="0.01" placeholder="Marks" class="examv2-input max-w-[160px]"></td></tr>`).join('')}</tbody></table></div>`:'<p class="text-xs text-gray-500">No students found in this batch.</p>'}catch(e){alert(e.message)}}
  async function saveResults(){try{const batch=v2Batch.value.trim(),subjects=v2Subjects.value.trim(),topics=v2Topics.value.trim(),exam_date=v2Date.value,overall=Number(v2Overall.value),ss=window.__v2students||[];if(!batch||!subjects||!topics||!exam_date||!overall||!ss.length)throw Error('Complete the paper details and load a batch first.');const students=ss.map(s=>({student_id:s.id,student_name:s.name,roll_number:s.roll_number||'',marks:(document.querySelector(`[data-v2-mark="${s.id}"]`)?.value||'')===''?null:Number(document.querySelector(`[data-v2-mark="${s.id}"]`).value)}));if(students.some(s=>s.marks!==null&&(s.marks<0||s.marks>overall)))throw Error(`Marks must be between 0 and ${overall}.`);await api('/api/examination/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:bid(),batch_name:batch,subjects,topics,exam_date,overall_marks:overall,students})});await api('/api/examination/history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:bid(),subject:subjects,topic:topics,batch_name:batch,exam_date})}).catch(()=>{});alert('Results saved.');await loadSavedResults()}catch(e){alert(e.message)}}
  async function loadSavedResults(){const el=document.getElementById('v2Saved');if(!el)return;try{const rows=await api(`/api/examination/results/${bid()}`);if(!rows.length){el.innerHTML='<p class="text-xs text-gray-500">No saved results yet.</p>';return}const groups={};rows.forEach(r=>{const k=[r.exam_date,r.batch_name,r.subjects,r.topics,r.overall_marks].join('|');(groups[k]??=[]).push(r)});el.innerHTML=Object.values(groups).map(g=>{const x=g[0];return `<div class="mb-8"><div class="grid md:grid-cols-5 gap-3 text-xs text-gray-400 mb-3"><span>Batch: <b class="text-gray-200">${escv(x.batch_name)}</b></span><span>Subject(s): <b class="text-gray-200">${escv(x.subjects)}</b></span><span>Topic(s): <b class="text-gray-200">${escv(x.topics)}</b></span><span>Date: <b class="text-gray-200">${escv(x.exam_date)}</b></span><span>Overall: <b class="text-gray-200">${escv(x.overall_marks)}</b></span></div><div class="overflow-x-auto"><table class="examv2-table"><thead><tr><th>Student</th><th>Roll Number</th><th>Marks / ${escv(x.overall_marks)}</th><th>Actions</th></tr></thead><tbody>${g.map(r=>`<tr><td>${escv(r.student_name)}</td><td>${escv(r.roll_number)}</td><td><input id="v2m-${r.id}" class="examv2-input max-w-[140px]" type="number" min="0" max="${escv(x.overall_marks)}" step="0.01" value="${r.marks??''}"></td><td class="whitespace-nowrap"><button class="examv2-btn" onclick="editV2Result(${r.id},${Number(x.overall_marks)})">Save</button> <button class="examv2-btn danger" onclick="deleteV2Result(${r.id})">Delete</button></td></tr>`).join('')}</tbody></table></div></div>`}).join('')}catch(e){el.innerHTML=`<p class="text-xs text-red-400">${escv(e.message)}</p>`}}
  window.editV2Result=async(id,overall)=>{const m=Number(document.getElementById(`v2m-${id}`).value);if(Number.isNaN(m)||m<0||m>overall)return alert(`Marks must be between 0 and ${overall}.`);try{await api(`/api/examination/results/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({marks:m})});await loadSavedResults()}catch(e){alert(e.message)}};
  window.deleteV2Result=async id=>{if(!confirm('Delete this result record?'))return;try{await api(`/api/examination/results/${id}`,{method:'DELETE'});await loadSavedResults()}catch(e){alert(e.message)}};
  async function pdfResults(){try{const rows=await api(`/api/examination/results/${bid()}`);if(!rows.length)return alert('No results to download.');const x=rows[0],{jsPDF}=window.jspdf,doc=new jsPDF();doc.setFontSize(17);doc.text('ALGORITHMIC — Examination Results',14,18);doc.setFontSize(10);doc.text(`Batch: ${x.batch_name}`,14,27);doc.text(`Subject(s): ${x.subjects}`,14,33);doc.text(`Topic(s): ${x.topics}`,14,39);doc.text(`Exam Date: ${x.exam_date}`,14,45);doc.text(`Overall Marks: ${x.overall_marks}`,14,51);doc.autoTable({startY:58,head:[['Student','Roll Number',`Marks / ${x.overall_marks}`]],body:rows.filter(r=>r.batch_name===x.batch_name&&r.subjects===x.subjects&&r.topics===x.topics&&r.exam_date===x.exam_date).map(r=>[r.student_name,r.roll_number||'',r.marks??'—'])});doc.save(`Results-${x.batch_name}-${x.exam_date}.pdf`)}catch(e){alert(e.message)}}
  window.openHistory=async function(){if(!isOwner&&!myAllowedModules.includes('examination'))return alert('You do not have Examination access.');const root=shell('History','Record the subject, topic, batch, and exam date for every examination.');root.insertAdjacentHTML('beforeend',`<div class="examv2-card"><div class="examv2-grid">${f('v2hSubject','Subject')}${f('v2hTopic','Topic')}${f('v2hBatch','Batch name')}${f('v2hDate','Exam date','date')}<div class="flex items-end"><button id="v2hAdd" class="examv2-btn primary w-full">Add Record</button></div></div><div class="flex gap-2 mt-3"><button id="v2hPdf" class="examv2-btn">Download PDF</button><button id="v2hRefresh" class="examv2-btn">Refresh</button></div></div><div class="examv2-card"><div class="flex justify-between items-center mb-4"><h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Exam History</h3><span class="text-xs text-gray-600">Editable and deletable</span></div><div id="v2History"></div></div>`);v2hAdd.onclick=addHistory;v2hPdf.onclick=pdfHistory;v2hRefresh.onclick=loadHistory;await loadHistory()};
  async function addHistory(){try{const subject=v2hSubject.value.trim(),topic=v2hTopic.value.trim(),batch_name=v2hBatch.value.trim(),exam_date=v2hDate.value;if(!subject||!topic||!batch_name||!exam_date)throw Error('Complete all history fields.');await api('/api/examination/history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:bid(),subject,topic,batch_name,exam_date})});v2hSubject.value=v2hTopic.value=v2hBatch.value=v2hDate.value='';await loadHistory()}catch(e){alert(e.message)}}
  async function loadHistory(){const el=document.getElementById('v2History');if(!el)return;try{const rows=await api(`/api/examination/history/${bid()}`);el.innerHTML=rows.length?`<div class="overflow-x-auto"><table class="examv2-table"><thead><tr><th>Subject</th><th>Topic</th><th>Batch</th><th>Date</th><th>Actions</th></tr></thead><tbody>${rows.map(r=>`<tr><td><input id="v2hs-${r.id}" class="examv2-input" value="${escv(r.subject)}"></td><td><input id="v2ht-${r.id}" class="examv2-input" value="${escv(r.topic)}"></td><td><input id="v2hb-${r.id}" class="examv2-input" value="${escv(r.batch_name)}"></td><td><input id="v2hd-${r.id}" type="date" class="examv2-input" value="${escv(r.exam_date)}"></td><td class="whitespace-nowrap"><button class="examv2-btn" onclick="editV2History(${r.id})">Save</button> <button class="examv2-btn danger" onclick="deleteV2History(${r.id})">Delete</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="text-xs text-gray-500">No examination history records yet.</p>'}catch(e){el.innerHTML=`<p class="text-xs text-red-400">${escv(e.message)}</p>`}}
  window.editV2History=async id=>{const subject=document.getElementById(`v2hs-${id}`).value.trim(),topic=document.getElementById(`v2ht-${id}`).value.trim(),batch_name=document.getElementById(`v2hb-${id}`).value.trim(),exam_date=document.getElementById(`v2hd-${id}`).value;if(!subject||!topic||!batch_name||!exam_date)return alert('All fields are required.');try{await api(`/api/examination/history/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:bid(),subject,topic,batch_name,exam_date})});await loadHistory()}catch(e){alert(e.message)}};
  window.deleteV2History=async id=>{if(!confirm('Delete this history record?'))return;try{await api(`/api/examination/history/${id}`,{method:'DELETE'});await loadHistory()}catch(e){alert(e.message)}};
  async function pdfHistory(){try{const rows=await api(`/api/examination/history/${bid()}`);if(!rows.length)return alert('No history records to download.');const{jsPDF}=window.jspdf,doc=new jsPDF();doc.setFontSize(17);doc.text('ALGORITHMIC — Examination History',14,18);doc.autoTable({startY:27,head:[['Subject','Topic','Batch','Exam Date']],body:rows.map(r=>[r.subject,r.topic,r.batch_name,r.exam_date])});doc.save(`Exam-History-${new Date().toISOString().slice(0,10)}.pdf`)}catch(e){alert(e.message)}}
})();
</script>
'''
    index = index.replace('</body>', frontend + '\n</body>', 1)
    index_path.write_text(index,encoding='utf-8')
