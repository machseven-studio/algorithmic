from pathlib import Path

# Backend: branch rename/delete (branch deletion cascades to every branch-owned table).
p = Path('main.py')
s = p.read_text(encoding='utf-8')
if '@app.delete("/api/branches/{branch_id}")' not in s:
    marker='@app.get("/api/records/{module}/{branch_id}")'
    api='''@app.patch("/api/branches/{branch_id}")
def edit_branch(branch_id: int, branch: BranchCreate, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(branch_id, institute.id)
    name=branch.name.strip()
    if not name: raise HTTPException(status_code=400, detail="Branch name cannot be empty")
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("SELECT id,name FROM branches WHERE id=%s AND tenant_id=%s",(branch_id,institute.id))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="Branch not found")
        cur.execute("UPDATE branches SET name=%s WHERE id=%s AND tenant_id=%s RETURNING id,name",(name,branch_id,institute.id)); row=cur.fetchone(); conn.commit(); return {"id":row[0],"name":row[1]}
    except psycopg2.IntegrityError:
        conn.rollback(); raise HTTPException(status_code=400, detail="A branch with that name already exists")
    finally: conn.close()

@app.delete("/api/branches/{branch_id}")
def delete_branch(branch_id: int, institute: CurrentInstitute = Depends(require_write_access)):
    verify_branch_ownership(branch_id, institute.id)
    conn=get_conn()
    try:
        cur=conn.cursor(); cur.execute("SELECT id,name FROM branches WHERE id=%s AND tenant_id=%s",(branch_id,institute.id)); row=cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="Branch not found")
        name=row["name"]; cur.execute("DELETE FROM branches WHERE id=%s AND tenant_id=%s",(branch_id,institute.id))
        if cur.rowcount!=1: raise HTTPException(status_code=404, detail="Branch not found")
        conn.commit(); return {"status":"deleted","id":branch_id,"name":name,"data_wiped":True}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

'''
    s=s.replace(marker,api+marker,1)
if '@app.delete("/api/assistant/history/{branch_id}")' not in s:
    marker='\n# ---------------------------------------------------------------------------\n# Frontend\n# ---------------------------------------------------------------------------'
    api='''\n@app.delete("/api/assistant/history/{branch_id}")
def clear_parallax_history(branch_id: int, institute: CurrentInstitute = Depends(get_current_institute)):
    check_module_access(institute,"assistant")
    verify_branch_read_access(branch_id,institute.id)
    return {"status":"cleared","persistent_history":False}
'''
    s=s.replace(marker,api+marker,1)
# Backend: hard seating constraint solver.
a=s.find('def _build_seating_layout(students, rows, columns):')
b=s.find('\n\n\n@app.get("/api/seating/{branch_id}")',a)
if a>=0 and b>a:
    solver='''def _build_seating_layout(students, rows, columns):
    capacity=rows*columns; selected=list(students[:capacity])
    if not selected: return []
    buckets=defaultdict(list)
    for st in selected: buckets[str(st.get("batch") or "").strip()].append(st)
    for v in buckets.values(): random.shuffle(v)
    if max(map(len,buckets.values()))>(capacity+1)//2:
        raise HTTPException(status_code=400,detail="The seating constraints cannot be satisfied: one batch has too many students for this grid.")
    grid=[[None]*columns for _ in range(rows)]; pos=[(r,c) for r in range(rows) for c in range(columns)]
    def ok(st,r,c):
        batch=str(st.get("batch") or "").strip()
        left=c and grid[r][c-1] is not None and str(grid[r][c-1].get("batch") or "").strip()==batch
        front=r and grid[r-1][c] is not None and str(grid[r-1][c].get("batch") or "").strip()==batch
        return not (left or front)
    def solve(i=0):
        if i==len(pos): return True
        r,c=pos[i]; choices=[v for v in buckets.values() if v]; random.shuffle(choices); choices.sort(key=len,reverse=True)
        for v in choices:
            st=v.pop()
            if ok(st,r,c):
                grid[r][c]=st
                if solve(i+1): return True
                grid[r][c]=None
            v.append(st)
        return False
    if not solve():
        raise HTTPException(status_code=400,detail="The seating constraints cannot be satisfied with the selected students and grid size. Increase the grid size or use more than one batch.")
    return [{"row":r+1,"column":c+1,"student_id":grid[r][c]["id"],"name":grid[r][c]["name"],"batch":grid[r][c]["batch"],"roll_number":grid[r][c]["roll_number"]} for r in range(rows) for c in range(columns) if grid[r][c] is not None]
'''
    s=s[:a]+solver+s[b:]
p.write_text(s,encoding='utf-8')

# Frontend: robust runtime compatibility layer.
p=Path('index.html'); s=p.read_text(encoding='utf-8')
css='''<style id="algorithmic-requested-fixes-css">
#branchSelector{background:#17140b!important;border:1px solid rgba(232,199,103,.5)!important;border-radius:10px;padding:8px 12px;color:#f0d77c!important;min-width:170px}#branchSelector option{background:#10100d;color:#f5e8b0}
#moduleGroups .sidebar-item{padding-left:1rem!important;margin-left:0!important;justify-content:flex-start!important;gap:0!important;border-left:0!important}#moduleGroups .sidebar-item>span:first-child{display:none!important}
#moduleSidebar.sidebar-collapsed{width:64px!important;min-width:64px!important;flex-basis:64px!important}#moduleSidebar.sidebar-collapsed>div:not(#moduleGroups),#moduleSidebar.sidebar-collapsed #moduleGroups{display:none!important}#moduleSidebar.sidebar-collapsed .sidebar-item,#moduleSidebar.sidebar-collapsed .module-head-group{display:none!important}
.parallax-thread{background:radial-gradient(circle at 20% 0,rgba(212,175,55,.07),transparent 40%),linear-gradient(155deg,#100e09,#070707)!important;border-color:rgba(232,199,103,.28)!important;box-shadow:0 24px 60px rgba(0,0,0,.35)!important;min-height:120px}#parallaxForm{padding:10px!important;border:1px solid rgba(212,175,55,.18)!important;border-radius:16px!important;background:rgba(12,12,12,.8)!important}#parallaxInput{min-height:48px!important;font-size:13px!important}
.parallax-search-shell{margin-top:1rem;padding:18px;border-radius:22px;border:1px solid rgba(232,199,103,.22);background:radial-gradient(circle at 10% 0,rgba(212,175,55,.08),transparent 38%),linear-gradient(145deg,#11100d,#080808);box-shadow:0 24px 60px rgba(0,0,0,.32)}.parallax-search-kicker{font-size:9px;letter-spacing:.2em;color:#8f8567;font-weight:800;margin-bottom:10px}.parallax-search-row{display:flex;align-items:center;gap:10px}.parallax-search-row input{flex:1;background:#060606;border:1px solid rgba(212,175,55,.25);border-radius:13px;padding:13px 14px;color:#eee;outline:none}.parallax-search-row button{background:#c6a64b;color:#080808;border:0;border-radius:12px;padding:12px 18px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.12em}.parallax-search-results{margin-top:10px;border-top:1px solid rgba(255,255,255,.05);padding-top:8px}.parallax-search-hit{display:grid;grid-template-columns:150px 1fr;gap:5px 14px;width:100%;text-align:left;padding:11px 12px;border-radius:10px;background:#0d0d0d;border:1px solid transparent;color:#ddd}.parallax-search-hit:hover{border-color:rgba(212,175,55,.18);background:#12110e}.parallax-search-hit span{font-size:9px;text-transform:uppercase;letter-spacing:.12em;color:#8c815e}.parallax-search-hit b{font-size:12px}.parallax-search-hit small{grid-column:1/-1;color:#777;font-size:10px}
#teacherConfigList{min-height:520px!important;max-height:70vh!important;padding:14px!important}.glass-panel:has(#teacherConfigList){width:min(94vw,1280px)!important;max-width:1280px!important;min-height:720px!important}.glass-panel:has(#teacherConfigList) #teacherConfigList>div{min-height:150px!important;padding:20px!important;gap:20px!important}.glass-panel:has(#teacherConfigList) #teacherConfigList input{min-height:48px!important;font-size:14px!important}
#examV2Root,#examUpgradeRoot{font-family:'Plus Jakarta Sans',sans-serif!important;color:#d1d5db!important}#examV2Root h1,#examV2Root h2,#examV2Root h3,#examUpgradeRoot h1,#examUpgradeRoot h2,#examUpgradeRoot h3{font-family:'Fraunces',serif!important;color:#c7aa56!important}#examV2Root th,#examUpgradeRoot th{color:#8f8567!important}#examV2Root td,#examUpgradeRoot td{color:#d1d5db!important}
</style>'''
if 'algorithmic-requested-fixes-css' not in s:s=s.replace('</head>',css+'\n</head>',1)

js=r'''<script id="algorithmic-requested-fixes-runtime">
(function(){
  const originalLoadBranches=loadBranches;
  loadBranches=async function(){await originalLoadBranches();const mc=branches.find(b=>String(b.name||'').trim().toLowerCase()==='main campus');const current=branches.find(b=>Number(b.id)===Number(currentBranchId));if(mc&&!current)currentBranchId=mc.id;const sel=document.getElementById('branchSelector');if(sel)sel.value=currentBranchId;const wrap=sel?.parentElement;if(wrap&&!document.getElementById('branchEditRuntime')){const mk=(id,text,fn,cls)=>{const b=document.createElement('button');b.id=id;b.textContent=text;b.className='ml-2 text-xs px-2.5 py-1 rounded-lg border '+cls;b.onclick=fn;return b};wrap.append(mk('branchEditRuntime','Edit',editBranchRuntime,'border-yellow-700/60 text-yellow-300 bg-[#25200f]'),mk('branchDeleteRuntime','Delete',deleteBranchRuntime,'border-red-700/50 text-red-300 bg-[#241111]'));}};
  async function editBranchRuntime(){const b=branches.find(x=>Number(x.id)===Number(currentBranchId));if(!b||myPermission==='read_only')return;const n=prompt('Rename branch:',b.name||'');if(n===null||!n.trim()||n.trim()===b.name)return;const r=await authFetch(`/api/branches/${b.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n.trim()})});const d=await r.json().catch(()=>({}));if(!r.ok)return alert(d.detail||'Could not rename branch.');await loadBranches();}
  async function deleteBranchRuntime(){const b=branches.find(x=>Number(x.id)===Number(currentBranchId));if(!b||myPermission==='read_only')return;if(!confirm(`Delete "${b.name}"?\n\nALL students, teachers, classrooms, attendance, syllabus, timetables, seating plans, fees, results and history in this branch will be permanently wiped.\n\nThis cannot be undone.`))return;const r=await authFetch(`/api/branches/${b.id}`,{method:'DELETE'});const d=await r.json().catch(()=>({}));if(!r.ok)return alert(d.detail||'Could not delete branch.');currentBranchId=null;await loadBranches();refreshCurrentModule();}
  const oldHome=renderHomeModule;renderHomeModule=function(c){oldHome(c);setTimeout(installSearch,0)};
  function installSearch(){const main=document.getElementById('mainContent');if(!main||document.getElementById('homeParallaxSearch'))return;const hero=main.querySelector('.glass-panel');if(!hero)return;const w=document.createElement('div');w.id='homeParallaxSearch';w.className='parallax-search-shell';w.innerHTML='<div class="parallax-search-kicker">PARALLAX · INSTITUTE SEARCH</div><div class="parallax-search-row"><input id="homeParallaxSearchInput" placeholder="Search students, teachers, fees, attendance, classrooms…"><button type="button">Search</button></div><div id="homeParallaxSearchResults" class="parallax-search-results hidden"></div>';hero.after(w);w.querySelector('button').onclick=searchHome;w.querySelector('input').onkeydown=e=>{if(e.key==='Enter')searchHome()};}
  async function searchHome(){const q=document.getElementById('homeParallaxSearchInput')?.value.trim(),out=document.getElementById('homeParallaxSearchResults');if(!q||!out||!currentBranchId)return;out.classList.remove('hidden');out.innerHTML='<div class="text-xs text-gray-500 p-3">Searching institute data…</div>';const mods=[['students','Student Department'],['teachers','Teacher Department'],['classrooms','Classroom Department'],['syllabus','Syllabus'],['attendance','Attendance'],['fees','Fees']],hits=[];await Promise.all(mods.map(async([m,l])=>{try{const r=await authFetch(`/api/records/${m}/${currentBranchId}?search=${encodeURIComponent(q)}&page_size=10`);if(r.ok)(await r.json()).slice(0,4).forEach(x=>hits.push({m,l,x}))}catch(_){}}));out.innerHTML=hits.length?hits.map(h=>`<button class="parallax-search-hit" onclick="switchModule('${h.m}')"><span>${esc(h.l)}</span><b>${esc(h.x.name||h.x.student_name||h.x.subject||h.x.room_no||'Record')}</b><small>${esc(Object.entries(h.x).filter(([k])=>!['id','branch_id','document'].includes(k)).slice(0,4).map(([k,v])=>k+': '+v).join(' · '))}</small></button>`).join(''):'<div class="text-xs text-gray-500 p-3">No matching records found in this branch.</div>';}
  const oldParallax=renderParallaxThread;renderParallaxThread=function(){oldParallax();const t=document.getElementById('parallaxThread');if(!t)return;if(!window.parallaxHistory.length)t.innerHTML='';const f=document.getElementById('parallaxForm');if(f&&!document.getElementById('parallaxDeleteRuntime')){const b=document.createElement('button');b.id='parallaxDeleteRuntime';b.type='button';b.textContent='Delete Chat History';b.className='text-xs text-red-300 border border-red-900/40 bg-[#160d0d] px-3 py-2 rounded-lg';b.onclick=async()=>{if(!confirm('Delete this Parallax chat history?'))return;window.parallaxHistory=[];renderParallaxThread();try{await authFetch(`/api/assistant/history/${currentBranchId}`,{method:'DELETE'})}catch(_){}};f.parentElement.insertBefore(b,f)}};
  const oldHist=openAttendanceHistory;openAttendanceHistory=async function(name){const m=document.getElementById('attendanceHistoryModal'),body=document.getElementById('attendanceHistoryBody');document.getElementById('attendanceHistoryTitle').textContent='Attendance History · '+name;body.innerHTML='<p class="text-xs text-gray-500 p-4">Loading…</p>';m.classList.remove('hidden');try{const r=await authFetch(`/api/attendance/history/${currentBranchId}?student_name=${encodeURIComponent(name)}`),d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Failed to load attendance history.');const rows=Array.isArray(d)?d:(Array.isArray(d.history)?d.history:[]);if(!rows.length){body.innerHTML='<p class="text-xs text-gray-500 p-4">No attendance marked yet for this student.</p>';return}body.innerHTML='<div class="flex justify-between text-xs text-gray-400 px-1 pb-3 border-b gold-border mb-3"><span>'+rows.length+' days marked</span><span class="text-green-400">'+rows.filter(x=>String(x.status)==='Present').length+' present</span><span class="text-red-400">'+rows.filter(x=>String(x.status)==='Absent').length+' absent</span></div><div class="max-h-72 overflow-y-auto">'+rows.map(x=>'<div class="attendance-history-row"><span class="date">'+esc(String(x.date??''))+'</span><span class="status '+(String(x.status)==='Present'?'present':'absent')+'">'+esc(String(x.status??''))+'</span></div>').join('')+'</div>'}catch(e){body.innerHTML='<p class="text-xs text-red-400 p-4">'+esc(e.message||'Failed to load history.')+'</p>'}};
})();
</script>'''
if 'algorithmic-requested-fixes-runtime' not in s:s=s.replace('</body>',js+'\n</body>',1)
p.write_text(s,encoding='utf-8')
print('done')
