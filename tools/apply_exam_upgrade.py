from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
main_path = ROOT / 'main.py'
index_path = ROOT / 'index.html'

MAIN_MARKER = '# === EXAM RESULTS / HISTORY UPGRADE ==='
INDEX_MARKER = '<!-- === EXAM RESULTS / HISTORY UPGRADE === -->'

main_patch = r'''

# === EXAM RESULTS / HISTORY UPGRADE ===
# Persistent exam result and exam-history records. This block is intentionally
# additive so existing application code/data remains untouched.

EXAM_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS exam_results (
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
)
"""
EXAM_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS exam_history (
    id SERIAL PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    batch_name TEXT NOT NULL,
    exam_date TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

def _init_exam_upgrade_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(EXAM_RESULTS_TABLE)
        cur.execute(EXAM_HISTORY_TABLE)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_results_branch_batch ON exam_results(branch_id, batch_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exam_history_branch_date ON exam_history(branch_id, exam_date)")
        conn.commit()
    finally:
        conn.close()

_init_exam_upgrade_db()

class ExamResultPayload(BaseModel):
    branch_id: int
    batch_name: str
    subjects: str
    topics: str
    exam_date: str
    overall_marks: float
    student_id: int | None = None
    student_name: str
    roll_number: str | None = None
    marks: float | None = None

class ExamHistoryPayload(BaseModel):
    branch_id: int
    subject: str
    topic: str
    batch_name: str
    exam_date: str

def _exam_branch_check(institute, branch_id: int):
    check_module_access(institute, "examination")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM branches WHERE id=%s AND institute_id=%s", (branch_id, institute.id))
        if not cur.fetchone():
            raise HTTPException(status_code=403, detail="Branch access denied")
    finally:
        conn.close()

def _valid_marks(marks, overall):
    if marks is None:
        return None
    if marks < 0 or marks > overall:
        raise HTTPException(status_code=400, detail="Student marks must be between 0 and the overall marks.")
    return marks

@app.get("/api/exam/results/students/{branch_id}")
def exam_result_students(branch_id: int, batch: str, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, branch_id)
    if not batch.strip():
        raise HTTPException(status_code=400, detail="Batch is required")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, COALESCE(roll_number, '') AS roll_number
            FROM students
            WHERE branch_id=%s AND LOWER(TRIM(COALESCE(batch,'')))=LOWER(TRIM(%s))
            ORDER BY LOWER(COALESCE(name,'')), LOWER(COALESCE(roll_number,''))
        """, (branch_id, batch))
        return {"students": [dict(r) for r in cur.fetchall()]}
    finally:
        conn.close()

@app.get("/api/exam/results/{branch_id}")
def list_exam_results(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, branch_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, branch_id, batch_name, subjects, topics, exam_date,
                   overall_marks, student_id, student_name, roll_number, marks,
                   created_at, updated_at
            FROM exam_results WHERE branch_id=%s
            ORDER BY exam_date DESC, batch_name, student_name, id
        """, (branch_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.post("/api/exam/results")
def create_exam_result(payload: ExamResultPayload, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, payload.branch_id)
    overall = float(payload.overall_marks)
    if overall <= 0:
        raise HTTPException(status_code=400, detail="Overall marks must be greater than zero.")
    marks = _valid_marks(payload.marks, overall)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO exam_results
            (branch_id,batch_name,subjects,topics,exam_date,overall_marks,student_id,student_name,roll_number,marks)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (payload.branch_id,payload.batch_name.strip(),payload.subjects.strip(),payload.topics.strip(),payload.exam_date,
              overall,payload.student_id,payload.student_name.strip(),payload.roll_number,marks))
        row = dict(cur.fetchone()); conn.commit(); return row
    finally:
        conn.close()

@app.patch("/api/exam/results/{result_id}")
def update_exam_result(result_id: int, payload: ExamResultPayload, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, payload.branch_id)
    marks = _valid_marks(payload.marks, float(payload.overall_marks))
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE exam_results SET batch_name=%s, subjects=%s, topics=%s, exam_date=%s,
              overall_marks=%s, student_id=%s, student_name=%s, roll_number=%s, marks=%s, updated_at=NOW()
            WHERE id=%s AND branch_id=%s RETURNING *
        """, (payload.batch_name.strip(),payload.subjects.strip(),payload.topics.strip(),payload.exam_date,
              payload.overall_marks,payload.student_id,payload.student_name.strip(),payload.roll_number,marks,result_id,payload.branch_id))
        row = cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="Result record not found")
        result = dict(row); conn.commit(); return result
    finally:
        conn.close()

@app.delete("/api/exam/results/{result_id}")
def delete_exam_result(result_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, "examination")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM exam_results WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE institute_id=%s) RETURNING id", (result_id,institute.id))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="Result record not found")
        conn.commit(); return {"status":"success"}
    finally:
        conn.close()

@app.get("/api/exam/history/{branch_id}")
def list_exam_history(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, branch_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, branch_id, subject, topic, batch_name, exam_date, created_at, updated_at FROM exam_history WHERE branch_id=%s ORDER BY exam_date DESC, id DESC", (branch_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

@app.post("/api/exam/history")
def create_exam_history(payload: ExamHistoryPayload, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, payload.branch_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO exam_history (branch_id,subject,topic,batch_name,exam_date) VALUES (%s,%s,%s,%s,%s) RETURNING *", (payload.branch_id,payload.subject.strip(),payload.topic.strip(),payload.batch_name.strip(),payload.exam_date))
        row=dict(cur.fetchone()); conn.commit(); return row
    finally:
        conn.close()

@app.patch("/api/exam/history/{history_id}")
def update_exam_history(history_id: int, payload: ExamHistoryPayload, institute: CurrentInstitute = Depends(get_current_institute)):
    _exam_branch_check(institute, payload.branch_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE exam_history SET subject=%s, topic=%s, batch_name=%s, exam_date=%s, updated_at=NOW() WHERE id=%s AND branch_id=%s RETURNING *", (payload.subject.strip(),payload.topic.strip(),payload.batch_name.strip(),payload.exam_date,history_id,payload.branch_id))
        row=cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="History record not found")
        result=dict(row); conn.commit(); return result
    finally:
        conn.close()

@app.delete("/api/exam/history/{history_id}")
def delete_exam_history(history_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute, "examination")
    conn = get_conn()
    try:
        cur=conn.cursor()
        cur.execute("DELETE FROM exam_history WHERE id=%s AND branch_id IN (SELECT id FROM branches WHERE institute_id=%s) RETURNING id", (history_id,institute.id))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="History record not found")
        conn.commit(); return {"status":"success"}
    finally:
        conn.close()
'''

index_patch = r'''
<!-- === EXAM RESULTS / HISTORY UPGRADE === -->
<script>
(() => {
  const HEAD = 'examination';
  const MODULES = { results: 'Results', history: 'History' };
  let originalSwitch = null;
  let originalRefresh = null;
  let examRoot = null;
  let editingResult = null;
  let editingHistory = null;

  const escExam = (v) => {
    if (typeof esc === 'function') return esc(v ?? '');
    return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  };
  const api = async (url, options={}) => {
    const r = await authFetch(url, options);
    const text = await r.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {detail:text}; }
    if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
    return data;
  };
  const branchId = () => {
    if (typeof currentBranchId !== 'undefined' && currentBranchId) return currentBranchId;
    const el = document.querySelector('[data-branch-id].active, select[id*=branch]');
    return el?.value || el?.dataset?.branchId || 1;
  };
  const contentHost = () => document.querySelector('main') || document.querySelector('#mainContent') || document.querySelector('#content') || document.body;

  function addExamSidebarItems() {
    document.querySelectorAll('button').forEach(btn => {
      const label = (btn.textContent || '').replace(/\s+/g,' ').trim();
      if (label === 'Exam Seating') btn.childNodes.forEach(n => { if (n.nodeType === 3) n.textContent = n.textContent.replace('Exam Seating','Seating'); });
      if (label === 'Exam Invigilation') btn.childNodes.forEach(n => { if (n.nodeType === 3) n.textContent = n.textContent.replace('Exam Invigilation','Invigilation'); });
    });
    const seating = [...document.querySelectorAll('button')].find(b => /^(?:🪑\s*)?Seating$/i.test((b.textContent||'').trim()) || /Exam Seating/i.test(b.textContent||''));
    const inv = [...document.querySelectorAll('button')].find(b => /Invigilation/i.test(b.textContent||''));
    if (!seating || !inv || document.querySelector('[data-exam-extra="results"]')) return;
    const parent = inv.parentElement || seating.parentElement;
    if (!parent) return;
    const make = (name, label) => {
      const b = document.createElement('button');
      b.type='button'; b.dataset.examExtra=name; b.className=seating.className || 'sidebar-item w-full text-left px-6 py-3 text-xs font-extrabold uppercase text-gray-300';
      b.innerHTML=`<span>◈</span><span>${label}</span>`;
      b.onclick=()=>window.switchModule(name,b);
      return b;
    };
    parent.appendChild(make('results','Results'));
    parent.appendChild(make('history','History'));
  }

  function header(title, subtitle='') {
    return `<div class="mb-6 flex items-start justify-between gap-4"><div><div class="text-[10px] uppercase tracking-[.2em] text-yellow-600 font-bold">Examination</div><h1 class="text-3xl font-black text-gray-100 mt-1">${title}</h1><p class="text-sm text-gray-500 mt-2">${subtitle}</p></div></div>`;
  }
  function card(inner) { return `<div class="bg-[#0b0b0b] border border-[#29230f] rounded-2xl p-5 shadow-2xl">${inner}</div>`; }
  function field(id,label,type='text',placeholder='') { return `<label class="block"><span class="block text-[10px] uppercase tracking-widest text-gray-500 font-bold mb-2">${label}</span><input id="${id}" type="${type}" placeholder="${placeholder}" class="w-full bg-[#050505] border border-gray-800 rounded-xl px-3 py-3 text-sm text-gray-200 outline-none focus:border-yellow-700"></label>`; }

  async function renderResults() {
    const host=contentHost(); examRoot=document.createElement('div'); examRoot.id='examUpgradeRoot';
    host.innerHTML=''; host.appendChild(examRoot);
    examRoot.innerHTML = header('Results','Enter the paper details, then record marks for every student in the selected batch.') + card(`
      <div class="grid md:grid-cols-5 gap-3">
        ${field('erBatch','Batch name','text','e.g. JEE 2026-A')}
        ${field('erSubjects','Subject(s)','text','Physics, Chemistry')}
        ${field('erTopics','Topic(s)','text','Kinematics, NLM')}
        ${field('erDate','Exam date','date')}
        ${field('erOverall','Overall marks','number','100')}
      </div>
      <div class="mt-4 flex gap-2"><button id="erLoad" class="px-4 py-2 rounded-xl bg-yellow-700/20 border border-yellow-800 text-yellow-500 font-bold text-xs uppercase tracking-widest">Load Students</button><button id="erPdf" class="px-4 py-2 rounded-xl border border-gray-800 text-gray-300 font-bold text-xs uppercase tracking-widest">Download PDF</button></div>
      <div id="erMeta" class="mt-5 hidden p-4 rounded-xl bg-[#080808] border border-gray-900 grid md:grid-cols-4 gap-4 text-sm"></div>
      <div id="erTable" class="mt-5 overflow-x-auto"></div>`);
    document.getElementById('erLoad').onclick=loadResultsStudents;
    document.getElementById('erPdf').onclick=pdfResults;
    await loadExistingResultGroups();
  }

  async function loadResultsStudents() {
    try {
      const batch=document.getElementById('erBatch').value.trim(), overall=Number(document.getElementById('erOverall').value);
      if(!batch || !overall || overall<=0) throw new Error('Enter batch name and a valid overall marks value.');
      const students=await api(`/api/exam/results/students/${encodeURIComponent(branchId())}?batch=${encodeURIComponent(batch)}`);
      const meta={batch,subjects:document.getElementById('erSubjects').value.trim(),topics:document.getElementById('erTopics').value.trim(),date:document.getElementById('erDate').value,overall};
      window.__examResultMeta=meta; window.__examStudents=students.students||[];
      document.getElementById('erMeta').classList.remove('hidden');
      document.getElementById('erMeta').innerHTML=`<div><b class="text-gray-500">Batch</b><div>${escExam(meta.batch)}</div></div><div><b class="text-gray-500">Subject(s)</b><div>${escExam(meta.subjects)}</div></div><div><b class="text-gray-500">Topic(s)</b><div>${escExam(meta.topics)}</div></div><div><b class="text-gray-500">Exam date</b><div>${escExam(meta.date)}</div></div>`;
      document.getElementById('erTable').innerHTML=`<table id="erStudentTable" class="w-full text-sm"><thead><tr class="text-left text-[10px] uppercase tracking-widest text-gray-500 border-b border-gray-900"><th class="p-3">Student</th><th class="p-3">Roll Number</th><th class="p-3">Marks / ${overall}</th><th class="p-3">Action</th></tr></thead><tbody>${(students.students||[]).map(s=>`<tr data-student="${s.id}" class="border-b border-gray-950"><td class="p-3 text-gray-200">${escExam(s.name)}</td><td class="p-3 text-gray-500">${escExam(s.roll_number)}</td><td class="p-3"><input data-mark-for="${s.id}" type="number" min="0" max="${overall}" step="0.01" class="w-32 bg-[#050505] border border-gray-800 rounded-lg px-3 py-2" placeholder="Marks"></td><td class="p-3"><button onclick="saveExamStudent(${s.id})" class="text-yellow-600 font-bold text-xs">SAVE</button></td></tr>`).join('')}</tbody></table>`;
    } catch(e) { alert(e.message); }
  }
  window.saveExamStudent=async(id)=>{ try { const s=(window.__examStudents||[]).find(x=>x.id===id), m=document.querySelector(`[data-mark-for="${id}"]`).value; if(m==='') throw new Error('Enter marks first.'); const meta=window.__examResultMeta; await api('/api/exam/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:Number(branchId()),batch_name:meta.batch,subjects:meta.subjects,topics:meta.topics,exam_date:meta.date,overall_marks:meta.overall,student_id:id,student_name:s.name,roll_number:s.roll_number,marks:Number(m)})}); alert('Result saved.'); await loadExistingResultGroups(); } catch(e){ alert(e.message); } };

  async function loadExistingResultGroups(){
    try { const rows=await api(`/api/exam/results/${encodeURIComponent(branchId())}`); window.__examResultRows=rows; const host=document.getElementById('erTable'); if(!host) return; if(!rows.length) return; host.insertAdjacentHTML('beforeend',`<div class="mt-8"><div class="flex justify-between items-center mb-3"><h3 class="font-bold text-gray-300">Saved Records</h3><button onclick="pdfResults()" class="text-xs text-yellow-600 font-bold">DOWNLOAD PDF</button></div><div class="overflow-x-auto"><table id="erSavedTable" class="w-full text-sm"><thead><tr class="text-left text-[10px] uppercase tracking-widest text-gray-500 border-b border-gray-900"><th class="p-3">Batch</th><th class="p-3">Subject(s)</th><th class="p-3">Topic(s)</th><th class="p-3">Date</th><th class="p-3">Student</th><th class="p-3">Marks</th><th class="p-3">Actions</th></tr></thead><tbody>${rows.map(r=>`<tr class="border-b border-gray-950"><td class="p-3">${escExam(r.batch_name)}</td><td class="p-3">${escExam(r.subjects)}</td><td class="p-3">${escExam(r.topics)}</td><td class="p-3">${escExam(r.exam_date)}</td><td class="p-3">${escExam(r.student_name)}</td><td class="p-3">${r.marks ?? '—'} / ${r.overall_marks}</td><td class="p-3 whitespace-nowrap"><button onclick="editExamResult(${r.id})" class="text-yellow-600 mr-3 text-xs font-bold">EDIT</button><button onclick="deleteExamResult(${r.id})" class="text-red-500 text-xs font-bold">DELETE</button></td></tr>`).join('')}</tbody></table></div></div>`); } catch(e) { console.error(e); }
  }
  window.deleteExamResult=async(id)=>{if(!confirm('Delete this result record?'))return;try{await api(`/api/exam/results/${id}`,{method:'DELETE'});await renderResults();}catch(e){alert(e.message)}};
  window.editExamResult=async(id)=>{const r=(window.__examResultRows||[]).find(x=>x.id===id);if(!r)return;document.getElementById('erBatch').value=r.batch_name;document.getElementById('erSubjects').value=r.subjects;document.getElementById('erTopics').value=r.topics;document.getElementById('erDate').value=r.exam_date;document.getElementById('erOverall').value=r.overall_marks;await loadResultsStudents();const inp=document.querySelector(`[data-mark-for="${r.student_id}"]`);if(inp)inp.value=r.marks??'';window.__examEditingId=id;document.getElementById('erLoad').textContent='UPDATE STUDENT';document.getElementById('erLoad').onclick=async()=>{try{const s=(window.__examStudents||[]).find(x=>x.id===r.student_id),m=Number(document.querySelector(`[data-mark-for="${r.student_id}"]`).value),meta=window.__examResultMeta;await api(`/api/exam/results/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:Number(branchId()),batch_name:meta.batch,subjects:meta.subjects,topics:meta.topics,exam_date:meta.date,overall_marks:Number(meta.overall),student_id:r.student_id,student_name:s.name,roll_number:s.roll_number,marks:m})});await renderResults();}catch(e){alert(e.message)}}};

  function pdfResults(){ const rows=window.__examResultRows||[]; if(!rows.length){alert('No saved result records to download.');return;} const J=window.jspdf; if(!J?.jsPDF){alert('PDF library is unavailable.');return;} const doc=new J.jsPDF('l'); doc.text('Examination Results',14,15); doc.autoTable({startY:22,head:[['Batch','Subject(s)','Topic(s)','Date','Student','Roll','Marks']],body:rows.map(r=>[r.batch_name,r.subjects,r.topics,r.exam_date,r.student_name,r.roll_number||'',`${r.marks??''} / ${r.overall_marks}`])});doc.save('exam-results.pdf'); }
  window.pdfResults=pdfResults;

  async function renderHistory(){const host=contentHost();examRoot=document.createElement('div');examRoot.id='examUpgradeRoot';host.innerHTML='';host.appendChild(examRoot);examRoot.innerHTML=header('History','Keep a clean record of every examination conducted.')+card(`<div class="grid md:grid-cols-4 gap-3">${field('ehSubject','Subject')}${field('ehTopic','Topic')}${field('ehBatch','Batch name')}${field('ehDate','Exam date','date')}</div><div class="mt-4 flex gap-2"><button id="ehSave" class="px-4 py-2 rounded-xl bg-yellow-700/20 border border-yellow-800 text-yellow-500 font-bold text-xs uppercase tracking-widest">Save Record</button><button onclick="pdfHistory()" class="px-4 py-2 rounded-xl border border-gray-800 text-gray-300 font-bold text-xs uppercase tracking-widest">Download PDF</button></div><div id="ehTable" class="mt-6 overflow-x-auto"></div>`);document.getElementById('ehSave').onclick=saveHistory;await loadHistory();}
  async function saveHistory(){try{const p={branch_id:Number(branchId()),subject:document.getElementById('ehSubject').value.trim(),topic:document.getElementById('ehTopic').value.trim(),batch_name:document.getElementById('ehBatch').value.trim(),exam_date:document.getElementById('ehDate').value};if(!p.subject||!p.topic||!p.batch_name||!p.exam_date)throw new Error('Fill all History fields.');if(window.__historyEditingId)await api(`/api/exam/history/${window.__historyEditingId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});else await api('/api/exam/history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});window.__historyEditingId=null;await loadHistory();}catch(e){alert(e.message)}}
  async function loadHistory(){try{const rows=await api(`/api/exam/history/${encodeURIComponent(branchId())}`);window.__examHistoryRows=rows;document.getElementById('ehTable').innerHTML=`<table id="ehSavedTable" class="w-full text-sm"><thead><tr class="text-left text-[10px] uppercase tracking-widest text-gray-500 border-b border-gray-900"><th class="p-3">Subject</th><th class="p-3">Topic</th><th class="p-3">Batch</th><th class="p-3">Exam date</th><th class="p-3">Actions</th></tr></thead><tbody>${rows.map(r=>`<tr class="border-b border-gray-950"><td class="p-3">${escExam(r.subject)}</td><td class="p-3">${escExam(r.topic)}</td><td class="p-3">${escExam(r.batch_name)}</td><td class="p-3">${escExam(r.exam_date)}</td><td class="p-3"><button onclick="editExamHistory(${r.id})" class="text-yellow-600 mr-3 text-xs font-bold">EDIT</button><button onclick="deleteExamHistory(${r.id})" class="text-red-500 text-xs font-bold">DELETE</button></td></tr>`).join('')}</tbody></table>`;}catch(e){console.error(e)}}
  window.editExamHistory=(id)=>{const r=(window.__examHistoryRows||[]).find(x=>x.id===id);if(!r)return;document.getElementById('ehSubject').value=r.subject;document.getElementById('ehTopic').value=r.topic;document.getElementById('ehBatch').value=r.batch_name;document.getElementById('ehDate').value=r.exam_date;window.__historyEditingId=id;document.getElementById('ehSave').textContent='Update Record';};
  window.deleteExamHistory=async(id)=>{if(!confirm('Delete this history record?'))return;try{await api(`/api/exam/history/${id}`,{method:'DELETE'});await loadHistory()}catch(e){alert(e.message)}};
  window.pdfHistory=()=>{const rows=window.__examHistoryRows||[];if(!rows.length){alert('No History records to download.');return;}const J=window.jspdf;if(!J?.jsPDF){alert('PDF library is unavailable.');return;}const doc=new J.jsPDF('l');doc.text('Examination History',14,15);doc.autoTable({startY:22,head:[['Subject','Topic','Batch','Exam Date']],body:rows.map(r=>[r.subject,r.topic,r.batch_name,r.exam_date])});doc.save('exam-history.pdf');};

  function install(){
    addExamSidebarItems();
    if(!originalSwitch && typeof window.switchModule==='function'){
      originalSwitch=window.switchModule;
      window.switchModule=async function(moduleName, btn){
        if(moduleName==='results'){currentModule='results';await renderResults();return;}
        if(moduleName==='history'){currentModule='history';await renderHistory();return;}
        if(examRoot){examRoot.remove();examRoot=null;}
        return originalSwitch.apply(this,arguments);
      };
    }
  }
  const observer=new MutationObserver(()=>install());
  observer.observe(document.documentElement,{childList:true,subtree:true});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
</script>
'''

main = main_path.read_text(encoding='utf-8')
if MAIN_MARKER not in main:
    main_path.write_text(main.rstrip() + main_patch + '\n', encoding='utf-8')

index = index_path.read_text(encoding='utf-8')
if INDEX_MARKER not in index:
    index_path.write_text(index.replace('</body>', INDEX_MARKER + '\n' + index_patch + '\n</body>', 1), encoding='utf-8')
