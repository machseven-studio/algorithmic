/* Algorithmic stability + UI fixes */
(() => {
  'use strict';

  const safe = (v) => typeof esc === 'function' ? esc(v ?? '') : String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = async (url, options = {}) => {
    const res = await authFetch(url, options);
    return readApiJson(res, 'Algorithmic request');
  };
  const text = (v) => {
    if (v == null) return '';
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
    if (v instanceof Date) return v.toISOString().slice(0,10);
    if (typeof v === 'object') return text(v.date ?? v.value ?? v.status ?? v.name ?? v.label ?? v.text ?? v.id ?? JSON.stringify(v));
    return String(v);
  };

  // Parallax: a slow AI request must never permanently lock the prompt UI.
  window.submitParallaxQuestion = async function(e) {
    e.preventDefault();
    const input = document.getElementById('parallaxInput');
    const errorEl = document.getElementById('parallaxError');
    const btn = document.getElementById('parallaxSubmitBtn');
    const question = (input?.value || '').trim();
    if (!question) return;
    if (!currentBranchId) { if (errorEl) errorEl.textContent = 'No branch selected.'; return; }
    if (errorEl) errorEl.textContent = '';
    if (input) input.value = '';
    if (btn) btn.disabled = true;
    const turn = { question, answer: '', pending: true };
    window.parallaxHistory = window.parallaxHistory || [];
    window.parallaxHistory.push(turn);
    if (typeof renderParallaxThread === 'function') renderParallaxThread();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const res = await authFetch(`/api/assistant/${currentBranchId}`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question}), signal:controller.signal });
      const data = await readApiJson(res, 'Parallax');
      turn.answer = text(data.answer) || '(no answer returned)';
    } catch (err) {
      turn.answer = err?.name === 'AbortError' ? 'Parallax timed out. The AI service took too long to respond; your session is still active, so you can ask again.' : `Couldn’t get an answer: ${text(err?.message || 'unknown error')}`;
    } finally {
      clearTimeout(timeout);
      turn.pending = false;
      if (btn) btn.disabled = false;
      if (typeof renderParallaxThread === 'function') renderParallaxThread();
    }
  };

  // Centralised analytics: use the Chart constructor through window and retry the CDN if needed.
  let chartLoader = null;
  function ensureChartJs() {
    if (window.Chart) return Promise.resolve(window.Chart);
    if (chartLoader) return chartLoader;
    chartLoader = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js';
      s.onload = () => window.Chart ? resolve(window.Chart) : reject(new Error('Chart.js loaded without a Chart global.'));
      s.onerror = () => reject(new Error('Chart.js could not be loaded. Refresh and retry analytics.'));
      document.head.appendChild(s);
    });
    return chartLoader;
  }
  window.renderCentralAnalyticsContent = async function(d) {
    const el = document.getElementById('centralAnalyticsContent');
    if (!el) return;
    const a=d?.attendance||{}, f=d?.fees||{}, trend=Array.isArray(a.trend)?a.trend:[], batches=Array.isArray(a.by_batch)?a.by_batch:[], revenue=Array.isArray(f.revenue)?f.revenue.slice().reverse():[];
    try { await ensureChartJs(); } catch (err) { el.innerHTML=`<div class="glass-panel border border-red-900/40 p-8 rounded-2xl text-center"><div class="text-red-400 font-bold mb-2">Analytics charts could not be loaded</div><div class="text-xs text-gray-500">${safe(err.message)}</div></div>`; return; }
    (window.__centralAnalyticsCharts||[]).forEach(c=>{try{c.destroy()}catch(_){}}); window.__centralAnalyticsCharts=[];
    el.innerHTML=`<div class="grid grid-cols-2 md:grid-cols-4 gap-4">${centralStatCard('Students',d.students_total)}${centralStatCard('Teachers',d.teachers_total)}${centralStatCard('Classrooms',d.classrooms_total)}${centralStatCard('Fees Pending','₹'+Number(f.pending_amount||0).toLocaleString('en-IN'))}</div><div class="grid md:grid-cols-2 gap-6"><div class="glass-panel border gold-border rounded-2xl p-6"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Attendance — Last 7 Days</div><canvas id="centralAttendanceChart" height="220"></canvas></div><div class="glass-panel border gold-border rounded-2xl p-6"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Attendance by Batch</div><canvas id="centralBatchChart" height="220"></canvas></div><div class="glass-panel border gold-border rounded-2xl p-6 md:col-span-2"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Fee Revenue (Recent)</div><canvas id="centralRevenueChart" height="180"></canvas></div></div>`;
    const C=window.Chart, scale={x:{ticks:{color:'#9ca3af'},grid:{color:'rgba(212,175,55,.12)'}},y:{ticks:{color:'#9ca3af'},grid:{color:'rgba(212,175,55,.12)'}}};
    const ac=document.getElementById('centralAttendanceChart'); if(ac) window.__centralAnalyticsCharts.push(new C(ac,{type:'line',data:{labels:trend.map(x=>text(x.label)),datasets:[{label:'Present %',data:trend.map(x=>Number(x.pct)||0),borderColor:'#d4af37',backgroundColor:'rgba(212,175,55,.15)',tension:.35,fill:true}]},options:{plugins:{legend:{labels:{color:'#9ca3af'}}},scales:{...scale,y:{...scale.y,min:0,max:100}}}}));
    const bc=document.getElementById('centralBatchChart'); if(bc) window.__centralAnalyticsCharts.push(new C(bc,{type:'bar',data:{labels:batches.map(x=>text(x.batch)),datasets:[{label:'Attendance %',data:batches.map(x=>Number(x.pct)||0),backgroundColor:'#d4af37'}]},options:{plugins:{legend:{display:false}},scales:{...scale,y:{...scale.y,min:0,max:100}}}}));
    const rc=document.getElementById('centralRevenueChart'); if(rc) window.__centralAnalyticsCharts.push(new C(rc,{type:'bar',data:{labels:revenue.map(x=>text(x.date)),datasets:[{label:'Revenue (₹)',data:revenue.map(x=>Number(x.amount)||0),backgroundColor:'rgba(212,175,55,.65)'}]},options:{plugins:{legend:{display:false}},scales:scale}}));
  };

  // Attendance history: normalize object-shaped values so [object Object] can never reach the UI.
  window.openAttendanceHistory = async function(studentName) {
    const modal=document.getElementById('attendanceHistoryModal'), body=document.getElementById('attendanceHistoryBody'), title=document.getElementById('attendanceHistoryTitle');
    if(title) title.textContent=`Attendance History · ${text(studentName)}`;
    if(body) body.innerHTML='<p class="text-xs text-gray-500 p-4">Loading…</p>';
    modal?.classList.remove('hidden');
    try {
      const data=await api(`/api/attendance/history/${currentBranchId}?student_name=${encodeURIComponent(text(studentName))}`), rows=Array.isArray(data.history)?data.history:[];
      if(!rows.length){if(body)body.innerHTML='<p class="text-xs text-gray-500 p-4">No attendance marked yet for this student.</p>';return;}
      if(body) body.innerHTML=`<div class="flex justify-between text-xs text-gray-400 px-1 pb-3 border-b gold-border mb-3"><span>${Number(data.total_marked)||rows.length} days marked</span><span class="text-green-400">${Number(data.present_count)||0} present</span><span class="text-red-400">${Number(data.absent_count)||0} absent</span></div><div class="max-h-72 overflow-y-auto divide-y divide-gray-900">${rows.map(h=>{const date=text(h?.date),status=text(h?.status),present=status.toLowerCase()==='present';return `<div class="flex justify-between items-center py-2 text-sm"><span class="text-gray-300">${safe(date)}</span><span class="font-semibold ${present?'text-green-400':'text-red-400'}">${safe(status)}</span></div>`}).join('')}</div>`;
    } catch(e) { if(body) body.innerHTML=`<p class="text-xs text-red-400 p-4">${safe(e.message||'Failed to load attendance history.')}</p>`; }
  };

  // Canonical left navigation. Results and History are normal Examination children.
  const ensureNav=()=>{
    const groups=document.getElementById('moduleGroups'); if(!groups)return;
    const base={homepage:[['analytics','◈','Analytics'],['assistant','✦','Parallax'],['students','🎓','Student Department'],['teachers','👨‍🏫','Teacher Department'],['classrooms','🏛️','Classroom Department'],['users','🔐','Manage Users']],administrations:[['attendance','📋','Attendance'],['syllabus','📚','Syllabus'],['timetables','🕒','Timetable'],['fees','💳','Fees'],['whatsapp','💬','WhatsApp Messaging']],examination:[['seating','🪑','Seating'],['invigilation','🛡️','Invigilation'],['results','▣','Results'],['history','◷','History']]};
    const heads=['homepage','administrations','examination'].filter(h=>isOwner||(myAllowedModules||[]).includes(h));
    groups.innerHTML=heads.map((head,i)=>{const children=base[head].filter(([m])=>m!=='users'||isOwner),open=head===(MODULE_HEAD[currentModule]||currentModule)||(i===0&&currentModule==='home');return `<div class="mb-1"><button id="head-btn-${head}" onclick="toggleModuleHead('${head}')" class="w-full flex items-center justify-between px-3 py-3 rounded-lg text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-yellow-500 hover:bg-[#111]"><span>${head==='homepage'?'Homepage':head==='administrations'?'Administrations':'Examination'}</span><span class="head-chevron ${open?'rotate-180':''}">⌄</span></button><div id="head-${head}" class="${open?'':'hidden'} space-y-0.5">${children.map(([m,icon,label])=>`<button data-module="${m}" onclick="switchModule('${m}', this)" class="sidebar-item w-full text-left px-6 py-2.5 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span class="w-5 text-center shrink-0">${icon}</span><span>${label}</span></button>`).join('')}</div></div>`}).join('');
    const active=groups.querySelector(`[data-module="${currentModule}"]`); if(active)active.classList.add('active');
  };
  window.renderModuleGroups=ensureNav;

  function examShell(title,subtitle){const host=document.getElementById('mainContent');host.innerHTML=`<div class="space-y-6"><div><h2 class="text-2xl font-black uppercase gold-gradient-text tracking-wide">${safe(title)}</h2><p class="text-xs text-gray-400 mt-1 uppercase tracking-widest">${safe(subtitle)}</p></div><div id="examModuleBody"></div></div>`;return document.getElementById('examModuleBody');}
  const examInput=(id,label,type='text',placeholder='')=>`<div><label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">${safe(label)}</label><input id="${id}" type="${type}" placeholder="${safe(placeholder)}" class="w-full bg-[#0c0c0c] border gold-border rounded-xl p-3 text-sm text-gray-200 gold-border-glow focus:outline-none"></div>`;

  async function openUnifiedResults(){
    currentModule='results';const root=examShell('Results','Examination results · consistent with the rest of Algorithmic');const canWrite=myPermission!=='read_only'&&!isGlobalView;
    root.innerHTML=`<div class="glass-panel border gold-border rounded-2xl p-6 shadow-2xl space-y-5"><div class="grid grid-cols-1 md:grid-cols-5 gap-4">${examInput('uBatch','Batch name','text','Batch A')}${examInput('uSubjects','Subject(s)','text','Physics, Chemistry')}${examInput('uTopics','Topic(s)','text','Kinematics')}${examInput('uDate','Exam date','date')}${examInput('uOverall','Overall marks','number','100')}</div><div class="flex flex-wrap gap-3">${canWrite?'<button id="uLoad" class="gold-bg text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider">Load Students</button>':''}<button id="uPdf" class="bg-[#141414] hover:bg-[#1f1f1f] gold-gradient-text border gold-border px-5 py-2.5 rounded-xl text-xs font-bold uppercase tracking-wider">Download PDF</button></div><div id="uMeta"></div><div id="uStudentTable"></div></div><div class="glass-panel border gold-border rounded-2xl p-6 shadow-2xl"><div class="flex justify-between items-center mb-4"><h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Saved Results</h3><button id="uRefresh" class="bg-[#141414] gold-gradient-text border gold-border px-4 py-2 rounded-lg text-xs font-bold uppercase">Refresh</button></div><div id="uSaved"></div></div>`;
    if(canWrite)document.getElementById('uLoad').onclick=async()=>{try{const batch=document.getElementById('uBatch').value.trim(),overall=Number(document.getElementById('uOverall').value);if(!batch||overall<=0)throw Error('Enter a batch and valid overall marks.');const rows=await api(`/api/examination/results/students/${Number(currentBranchId)}?batch=${encodeURIComponent(batch)}`);window.__uStudents=Array.isArray(rows)?rows:[];document.getElementById('uMeta').innerHTML=`<div class="text-xs text-gray-400 bg-[#0c0c0c] border gold-border rounded-xl p-4">Batch: <b class="text-gray-200">${safe(batch)}</b> · Subject(s): <b class="text-gray-200">${safe(document.getElementById('uSubjects').value)}</b> · Topic(s): <b class="text-gray-200">${safe(document.getElementById('uTopics').value)}</b> · Date: <b class="text-gray-200">${safe(document.getElementById('uDate').value)}</b></div>`;document.getElementById('uStudentTable').innerHTML=window.__uStudents.length?`<div class="overflow-x-auto"><table class="w-full text-left text-sm text-gray-300"><thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border"><tr><th class="p-4">Student</th><th class="p-4">Roll Number</th><th class="p-4">Marks / ${overall}</th><th class="p-4"></th></tr></thead><tbody>${window.__uStudents.map(s=>`<tr class="border-b border-gray-900"><td class="p-4 font-medium">${safe(s.name)}</td><td class="p-4 text-gray-400">${safe(s.roll_number)}</td><td class="p-4"><input id="um-${s.id}" type="number" min="0" max="${overall}" step="0.01" class="w-40 bg-[#0c0c0c] border gold-border rounded-lg p-2 text-sm text-gray-200"></td><td class="p-4"><button onclick="saveUnifiedResult(${s.id})" class="gold-gradient-text font-bold text-xs uppercase">Save</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="text-sm text-gray-500 py-6 text-center">No students found in this batch.</p>'}catch(e){document.getElementById('uStudentTable').innerHTML=`<p class="text-sm text-red-400">${safe(e.message)}</p>`}};
    document.getElementById('uPdf').onclick=async()=>{try{const rows=await api(`/api/examination/results/${Number(currentBranchId)}`);if(!rows.length)return alert('No saved result records to download.');const J=window.jspdf;if(!J?.jsPDF)return alert('PDF library is unavailable.');const doc=new J.jsPDF('l');doc.text('ALGORITHMIC — Examination Results',14,15);doc.autoTable({startY:22,head:[['Batch','Subject(s)','Topic(s)','Date','Student','Roll','Marks']],body:rows.map(r=>[r.batch_name,r.subjects,r.topics,r.exam_date,r.student_name,r.roll_number||'',`${r.marks??''} / ${r.overall_marks}`])});doc.save('exam-results.pdf')}catch(e){alert(e.message)}};
    document.getElementById('uRefresh').onclick=loadUnifiedSavedResults;await loadUnifiedSavedResults();
  }
  window.saveUnifiedResult=async(id)=>{try{if(isGlobalView||myPermission==='read_only')return;const s=(window.__uStudents||[]).find(x=>x.id===id),m=document.getElementById(`um-${id}`).value,batch=document.getElementById('uBatch').value.trim(),subjects=document.getElementById('uSubjects').value.trim(),topics=document.getElementById('uTopics').value.trim(),exam_date=document.getElementById('uDate').value,overall=Number(document.getElementById('uOverall').value);if(m==='')throw Error('Enter marks first.');const marks=Number(m);if(marks<0||marks>overall)throw Error(`Marks must be between 0 and ${overall}.`);await api('/api/examination/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({branch_id:Number(currentBranchId),batch_name:batch,subjects,topics,exam_date,overall_marks:overall,students:[{student_id:s.id,student_name:s.name,roll_number:s.roll_number||'',marks}]});await loadUnifiedSavedResults()}catch(e){alert(e.message)}};
  async function loadUnifiedSavedResults(){const el=document.getElementById('uSaved');if(!el)return;try{const rows=await api(`/api/examination/results/${Number(currentBranchId)}`);el.innerHTML=rows.length?`<div class="overflow-x-auto"><table class="w-full text-left text-sm text-gray-300"><thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border"><tr><th class="p-4">Batch</th><th class="p-4">Subject(s)</th><th class="p-4">Topic(s)</th><th class="p-4">Date</th><th class="p-4">Student</th><th class="p-4">Marks</th><th class="p-4"></th></tr></thead><tbody>${rows.map(r=>`<tr class="border-b border-gray-900"><td class="p-4">${safe(r.batch_name)}</td><td class="p-4">${safe(r.subjects)}</td><td class="p-4">${safe(r.topics)}</td><td class="p-4">${safe(r.exam_date)}</td><td class="p-4 font-medium">${safe(r.student_name)}</td><td class="p-4">${safe(r.marks)} / ${safe(r.overall_marks)}</td><td class="p-4 text-right">${myPermission!=='read_only'&&!isGlobalView?`<button onclick="deleteUnifiedResult(${r.id})" class="text-red-400 text-xs font-bold uppercase">Delete</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<p class="text-sm text-gray-500 py-6 text-center">No saved results yet.</p>'}catch(e){el.innerHTML=`<p class="text-sm text-red-400">${safe(e.message)}</p>`}}
  window.deleteUnifiedResult=async(id)=>{if(!confirm('Delete this result record?'))return;try{await api(`/api/examination/results/${id}`,{method:'DELETE'});await loadUnifiedSavedResults()}catch(e){alert(e.message)}};

  async function openUnifiedHistory(){currentModule='history';const root=examShell('History','Examination history · consistent with the rest of Algorithmic');const canWrite=myPermission!=='read_only'&&!isGlobalView;root.innerHTML=`<div class="glass-panel border gold-border rounded-2xl p-6 shadow-2xl space-y-5"><div class="grid grid-cols-1 md:grid-cols-4 gap-4">${examInput('hSubject','Subject')}${examInput('hTopic','Topic')}${examInput('hBatch','Batch name')}${examInput('hDate','Exam date','date')}</div><div class="flex flex-wrap gap-3">${canWrite?'<button id="hSave" class="gold-bg text-black font-extrabold px-5 py-2.5 rounded-xl text-xs uppercase tracking-wider">Add Record</button>':''}<button id="hPdf" class="bg-[#141414] gold-gradient-text border gold-border px-5 py-2.5 rounded-xl text-xs font-bold uppercase tracking-wider">Download PDF</button></div></div><div class="glass-panel border gold-border rounded-2xl p-6 shadow-2xl"><div class="flex justify-between items-center mb-4"><h3 class="text-sm font-extrabold gold-gradient-text uppercase tracking-wider">Exam History</h3><button id="hRefresh" class="bg-[#141414] gold-gradient-text border gold-border px-4 py-2 rounded-lg text-xs font-bold uppercase">Refresh</button></div><div id="hSaved"></div></div>`;if(canWrite)document.getElementById('hSave').onclick=async()=>{try{const p={branch_id:Number(currentBranchId),subject:document.getElementById('hSubject').value.trim(),topic:document.getElementById('hTopic').value.trim(),batch_name:document.getElementById('hBatch').value.trim(),exam_date:document.getElementById('hDate').value};if(!p.subject||!p.topic||!p.batch_name||!p.exam_date)throw Error('Complete all History fields.');await api('/api/examination/history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});['hSubject','hTopic','hBatch','hDate'].forEach(id=>document.getElementById(id).value='');await loadUnifiedHistory()}catch(e){alert(e.message)}};document.getElementById('hRefresh').onclick=loadUnifiedHistory;document.getElementById('hPdf').onclick=async()=>{try{const rows=await api(`/api/examination/history/${Number(currentBranchId)}`);if(!rows.length)return alert('No History records to download.');const J=window.jspdf;if(!J?.jsPDF)return alert('PDF library is unavailable.');const doc=new J.jsPDF('l');doc.text('ALGORITHMIC — Examination History',14,15);doc.autoTable({startY:22,head:[['Subject','Topic','Batch','Exam Date']],body:rows.map(r=>[r.subject,r.topic,r.batch_name,r.exam_date])});doc.save('exam-history.pdf')}catch(e){alert(e.message)}};await loadUnifiedHistory();}
  async function loadUnifiedHistory(){const el=document.getElementById('hSaved');if(!el)return;try{const rows=await api(`/api/examination/history/${Number(currentBranchId)}`);el.innerHTML=rows.length?`<div class="overflow-x-auto"><table class="w-full text-left text-sm text-gray-300"><thead class="bg-[#121212] text-xs uppercase gold-gradient-text border-b gold-border"><tr><th class="p-4">Subject</th><th class="p-4">Topic</th><th class="p-4">Batch</th><th class="p-4">Exam Date</th><th class="p-4"></th></tr></thead><tbody>${rows.map(r=>`<tr class="border-b border-gray-900"><td class="p-4">${safe(r.subject)}</td><td class="p-4">${safe(r.topic)}</td><td class="p-4">${safe(r.batch_name)}</td><td class="p-4">${safe(r.exam_date)}</td><td class="p-4 text-right">${myPermission!=='read_only'&&!isGlobalView?`<button onclick="deleteUnifiedHistory(${r.id})" class="text-red-400 text-xs font-bold uppercase">Delete</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<p class="text-sm text-gray-500 py-6 text-center">No examination history records yet.</p>'}catch(e){el.innerHTML=`<p class="text-sm text-red-400">${safe(e.message)}</p>`}}
  window.deleteUnifiedHistory=async(id)=>{if(!confirm('Delete this history record?'))return;try{await api(`/api/examination/history/${id}`,{method:'DELETE'});await loadUnifiedHistory()}catch(e){alert(e.message)}};

  const oldSwitch=window.switchModule;
  window.switchModule=async function(moduleName,clickedButton=null){if(['results','history'].includes(moduleName)){if(!isOwner&&!(myAllowedModules||[]).includes('examination'))return alert('Your account does not have access to that module.');currentModule=moduleName;document.querySelectorAll('.sidebar-item').forEach(b=>b.classList.remove('active'));clickedButton?.classList.add('active');return moduleName==='results'?openUnifiedResults():openUnifiedHistory();}return oldSwitch.call(this,moduleName,clickedButton)};

  // Stop legacy exam-upgrade scripts from leaving duplicate Results/History buttons behind.
  const scrub=()=>document.querySelectorAll('[data-exam-v2],[data-exam-extra]').forEach(el=>el.remove());
  setTimeout(()=>{scrub();ensureNav()},0);setTimeout(()=>{scrub();ensureNav()},250);setTimeout(()=>{scrub();ensureNav()},1000);

  const style=document.createElement('style');style.textContent=`#moduleSidebar{align-items:stretch!important}#moduleGroups{width:100%;padding:0 10px!important}#moduleGroups>div{width:100%;margin:0 0 2px!important}#moduleGroups .sidebar-item{box-sizing:border-box!important;width:100%!important;margin:0!important;padding-left:16px!important;padding-right:12px!important}#moduleGroups .sidebar-item:hover,#moduleGroups .sidebar-item.active{padding-left:16px!important}#moduleGroups .sidebar-item>span:first-child{width:20px!important;min-width:20px!important;text-align:center!important}#moduleGroups>div>button[id^="head-btn-"]{width:100%!important;text-align:left!important}`;document.head.appendChild(style);
})();
