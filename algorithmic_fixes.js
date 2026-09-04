/* Algorithmic stability + UI consistency fixes v2 */
(() => {
  'use strict';

  const valueText = (v) => {
    if (v == null) return '';
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
    if (typeof v === 'object') return valueText(v.name ?? v.full_name ?? v.student_name ?? v.date ?? v.value ?? v.status ?? v.label ?? v.text ?? v.id ?? JSON.stringify(v));
    return String(v);
  };
  const escValue = (v) => typeof esc === 'function' ? esc(v ?? '') : String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const api = async (url, options = {}) => {
    const res = await authFetch(url, options);
    const data = await readApiJson(res, 'Algorithmic request');
    return data;
  };

  /* ---------------- Parallax ---------------- */
  window.submitParallaxQuestion = async function (e) {
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
    window.parallaxHistory = window.parallaxHistory || [];
    const turn = { question, answer: '', pending: true };
    window.parallaxHistory.push(turn);
    if (typeof renderParallaxThread === 'function') renderParallaxThread();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const res = await authFetch(`/api/assistant/${currentBranchId}`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question}), signal:controller.signal });
      const data = await readApiJson(res, 'Parallax');
      turn.answer = valueText(data.answer) || '(no answer returned)';
    } catch (err) {
      turn.answer = err?.name === 'AbortError' ? 'Parallax timed out. The AI service took too long to respond; your session is still active, so you can ask again.' : `Couldn’t get an answer: ${valueText(err?.message || 'unknown error')}`;
    } finally {
      clearTimeout(timer); turn.pending = false; if (btn) btn.disabled = false;
      if (typeof renderParallaxThread === 'function') renderParallaxThread();
    }
  };

  /* ---------------- Centralised analytics ---------------- */
  let chartLoader = null;
  const ensureChartJs = () => {
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
  };
  window.renderCentralAnalyticsContent = async function (d) {
    const el = document.getElementById('centralAnalyticsContent'); if (!el) return;
    try {
      await ensureChartJs();
      const a=d?.attendance||{}, f=d?.fees||{}, trend=Array.isArray(a.trend)?a.trend:[], batches=Array.isArray(a.by_batch)?a.by_batch:[], revenue=Array.isArray(f.revenue)?f.revenue.slice().reverse():[];
      (window.__centralAnalyticsCharts||[]).forEach(c=>{try{c.destroy()}catch(_){}}); window.__centralAnalyticsCharts=[];
      el.innerHTML=`<div class="grid grid-cols-2 md:grid-cols-4 gap-4">${centralStatCard('Students',d.students_total)}${centralStatCard('Teachers',d.teachers_total)}${centralStatCard('Classrooms',d.classrooms_total)}${centralStatCard('Fees Pending','₹'+Number(f.pending_amount||0).toLocaleString('en-IN'))}</div><div class="grid md:grid-cols-2 gap-6"><div class="glass-panel border gold-border rounded-2xl p-6"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Attendance — Last 7 Days</div><canvas id="centralAttendanceChart" height="220"></canvas></div><div class="glass-panel border gold-border rounded-2xl p-6"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Attendance by Batch</div><canvas id="centralBatchChart" height="220"></canvas></div><div class="glass-panel border gold-border rounded-2xl p-6 md:col-span-2"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Fee Revenue (Recent)</div><canvas id="centralRevenueChart" height="180"></canvas></div></div>`;
      const C=window.Chart, scale={x:{ticks:{color:'#9ca3af'},grid:{color:'rgba(212,175,55,.12)'}},y:{ticks:{color:'#9ca3af'},grid:{color:'rgba(212,175,55,.12)'}}};
      const ac=document.getElementById('centralAttendanceChart'); if(ac) window.__centralAnalyticsCharts.push(new C(ac,{type:'line',data:{labels:trend.map(x=>valueText(x.label)),datasets:[{label:'Present %',data:trend.map(x=>Number(x.pct)||0),borderColor:'#d4af37',backgroundColor:'rgba(212,175,55,.15)',tension:.35,fill:true}]},options:{plugins:{legend:{labels:{color:'#9ca3af'}}},scales:{...scale,y:{...scale.y,min:0,max:100}}}}));
      const bc=document.getElementById('centralBatchChart'); if(bc) window.__centralAnalyticsCharts.push(new C(bc,{type:'bar',data:{labels:batches.map(x=>valueText(x.batch)),datasets:[{label:'Attendance %',data:batches.map(x=>Number(x.pct)||0),backgroundColor:'#d4af37'}]},options:{plugins:{legend:{display:false}},scales:{...scale,y:{...scale.y,min:0,max:100}}}}));
      const rc=document.getElementById('centralRevenueChart'); if(rc) window.__centralAnalyticsCharts.push(new C(rc,{type:'bar',data:{labels:revenue.map(x=>valueText(x.date)),datasets:[{label:'Revenue (₹)',data:revenue.map(x=>Number(x.amount)||0),backgroundColor:'rgba(212,175,55,.65)'}]},options:{plugins:{legend:{display:false}},scales:scale}}));
    } catch(err) { el.innerHTML=`<div class="glass-panel border border-red-900/40 p-8 rounded-2xl text-center"><div class="text-red-400 font-bold mb-2">Analytics charts could not be loaded</div><div class="text-xs text-gray-500">${escValue(err.message||'Chart.js unavailable')}</div></div>`; }
  };

  /* ---------------- Attendance history ---------------- */
  window.openAttendanceHistory = async function(studentRef) {
    const studentName=valueText(studentRef);
    const modal=document.getElementById('attendanceHistoryModal'), body=document.getElementById('attendanceHistoryBody'), title=document.getElementById('attendanceHistoryTitle');
    if(title) title.textContent=`Attendance History · ${studentName}`;
    if(body) body.innerHTML='<p class="text-xs text-gray-500 p-4">Loading…</p>';
    modal?.classList.remove('hidden');
    try {
      const data=await api(`/api/attendance/history/${currentBranchId}?student_name=${encodeURIComponent(studentName)}`);
      const rows=Array.isArray(data.history)?data.history:[];
      if(!rows.length){if(body)body.innerHTML='<p class="text-xs text-gray-500 p-4">No attendance marked yet for this student.</p>';return;}
      if(body)body.innerHTML=`<div class="flex justify-between text-xs text-gray-400 px-1 pb-3 border-b gold-border mb-3"><span>${Number(data.total_marked)||rows.length} days marked</span><span class="text-green-400">${Number(data.present_count)||0} present</span><span class="text-red-400">${Number(data.absent_count)||0} absent</span></div><div class="max-h-72 overflow-y-auto divide-y divide-gray-900">${rows.map(h=>{const date=valueText(h?.date),status=valueText(h?.status),present=status.toLowerCase()==='present';return `<div class="flex justify-between items-center py-2 text-sm"><span class="text-gray-300">${escValue(date)}</span><span class="font-semibold ${present?'text-green-400':'text-red-400'}">${escValue(status)}</span></div>`}).join('')}</div>`;
    } catch(err){if(body)body.innerHTML=`<p class="text-xs text-red-400 p-4">${escValue(err.message||'Failed to load attendance history.')}</p>`;}
  };

  /* ---------------- Canonical sidebar ---------------- */
  const MODULES={
    administrations:[['attendance','📋','Attendance'],['syllabus','📚','Syllabus'],['timetables','🕒','Timetable'],['fees','💳','Fees'],['whatsapp','💬','WhatsApp Messaging']],
    examination:[['seating','🪑','Seating'],['invigilation','🛡️','Invigilation'],['results','📊','Results'],['history','🗂️','History']]
  };
  const allowedHead=h=>isOwner||(myAllowedModules||[]).includes(h);
  const renderCanonicalSidebar=()=>{
    const groups=document.getElementById('moduleGroups'); if(!groups)return;
    const openHead=MODULE_HEAD[currentModule]||currentModule;
    const homeAllowed=allowedHead('homepage');
    groups.innerHTML=`${homeAllowed?`<button data-module="home" id="homeDashboardButton" class="sidebar-item w-full text-left px-4 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span class="module-icon">⌂</span><span>Homepage</span></button>`:''}`+
      ['administrations','examination'].filter(allowedHead).map(head=>{
        const children=MODULES[head].filter(([m])=>m!=='users'||isOwner);
        const open=openHead===head;
        return `<div class="module-head-group"><div class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg"><span class="text-[10px] font-black uppercase tracking-widest text-gray-400">${head==='administrations'?'Administrations':'Examination'}</span><button type="button" aria-label="Open ${head} menu" class="module-head-arrow px-2 py-1 text-gray-400 hover:text-yellow-500" data-head="${head}">${open?'⌃':'⌄'}</button></div><div id="head-${head}" class="${open?'':'hidden'} space-y-0.5">${children.map(([m,icon,label])=>`<button data-module="${m}" onclick="switchModule('${m}', this)" class="sidebar-item w-full text-left px-4 py-2.5 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span class="module-icon">${icon}</span><span>${label}</span></button>`).join('')}</div></div>`;
      }).join('');
    const home=document.getElementById('homeDashboardButton');
    if(home) home.onclick=()=>window.goHomeDashboard();
    groups.querySelectorAll('.module-head-arrow').forEach(btn=>btn.onclick=()=>toggleModuleHead(btn.dataset.head));
    const active=groups.querySelector(`[data-module="${currentModule}"]`); if(active)active.classList.add('active');
  };
  window.renderModuleGroups=renderCanonicalSidebar;

  /* Homepage is a real navigation target, not logout/login. */
  window.goHomeDashboard=async function(){
    currentModule='home';
    document.querySelectorAll('.sidebar-item').forEach(b=>b.classList.remove('active'));
    document.getElementById('homeDashboardButton')?.classList.add('active');
    try{ await refreshCurrentModule(); }finally{ renderCanonicalSidebar(); document.getElementById('homeDashboardButton')?.classList.add('active'); }
  };

  /* Preserve existing module renderers, but make Results/History use their v2 renderers. */
  const legacySwitch=window.switchModule;
  window.switchModule=async function(moduleName,clickedButton=null){
    if(moduleName==='home') return window.goHomeDashboard();
    if(moduleName==='results'||moduleName==='history'){
      if(!allowedHead('examination'))return alert('Your account does not have access to that module.');
      currentModule=moduleName;
      document.querySelectorAll('.sidebar-item').forEach(b=>b.classList.remove('active')); clickedButton?.classList.add('active');
      if(moduleName==='results'&&typeof window.openResults==='function')return window.openResults();
      if(moduleName==='history'&&typeof window.openHistory==='function')return window.openHistory();
    }
    return legacySwitch.call(this,moduleName,clickedButton);
  };

  /* ---------------- Sidebar collapse ---------------- */
  function installSidebarCollapse(){
    const sidebar=document.getElementById('moduleSidebar'); if(!sidebar||sidebar.dataset.collapseReady)return;
    sidebar.dataset.collapseReady='1';
    const button=document.createElement('button'); button.id='sidebarCollapseButton'; button.type='button'; button.title='Collapse navigation'; button.setAttribute('aria-label','Collapse navigation'); button.textContent='‹';
    sidebar.style.position='relative'; sidebar.appendChild(button);
    button.onclick=()=>{const collapsed=sidebar.classList.toggle('sidebar-collapsed');button.textContent=collapsed?'›':'‹';button.title=collapsed?'Open navigation':'Collapse navigation';document.body.classList.toggle('sidebar-is-collapsed',collapsed);};
  }

  /* ---------------- Dashboard clock / date ---------------- */
  function updateDashboardClock(){
    const el=document.getElementById('dashboardClockWidget'); if(!el)return;
    const now=new Date();
    const time=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true}).format(now);
    const date=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'2-digit',month:'long',year:'numeric'}).format(now);
    el.innerHTML=`<div class="clock-time gold-gradient-text">${escValue(time)} IST</div><div class="clock-date">${escValue(date)}</div>`;
  }
  function installDashboardClock(){
    if(document.getElementById('dashboardClockWidget'))return;
    const candidates=[...document.querySelectorAll('h1,h2,h3,p,div')].filter(el=>/^\s*WELCOME\b/i.test((el.textContent||'').trim()));
    const welcome=candidates.sort((a,b)=>(a.textContent.length-b.textContent.length))[0];
    if(!welcome)return;
    const host=welcome.parentElement||welcome;
    const wrap=document.createElement('div'); wrap.id='dashboardWelcomeRow';
    wrap.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;width:100%;';
    welcome.parentElement.insertBefore(wrap,welcome); wrap.appendChild(welcome);
    const clock=document.createElement('div'); clock.id='dashboardClockWidget'; wrap.appendChild(clock); updateDashboardClock(); setInterval(updateDashboardClock,1000);
  }

  /* ---------------- Timetable visibility + no seeded form values ---------------- */
  const clearTimetableDefaults=()=>{
    const batch=document.getElementById('ttBatchName'); if(batch && batch.value==='B.Tech CSE Batch A')batch.value='';
    const timing=document.getElementById('ttTimingRows'); if(timing && timing.children.length && [...timing.querySelectorAll('.tt-time-slot')].every(x=>/^(09:00 AM - 10:00 AM|10:00 AM - 11:00 AM|11:15 AM - 12:15 PM|01:15 PM - 02:15 PM)$/.test(x.value))) timing.innerHTML='';
    document.querySelectorAll('#teacherConfigList input[id^="lec_"]').forEach(i=>{if(i.value==='3')i.value='';});
  };
  const oldTimetable=window.renderTimetableModuleUnsafe;
  if(typeof oldTimetable==='function')window.renderTimetableModuleUnsafe=async function(container){const r=await oldTimetable(container);clearTimetableDefaults();return r;};

  /* ---------------- One-time clearing of seeded module records ----------------
     Keeps the institute/account itself intact. This runs once per browser session
     and uses the normal authenticated delete APIs, so tenant boundaries remain enforced. */
  async function clearSeededRecordsOnce(){
    const key='algorithmic_seed_records_cleared_v2';
    if(localStorage.getItem(key)||!currentBranchId)return;
    localStorage.setItem(key,'1');
    const modules=['students','teachers','classrooms','syllabus','attendance','invigilation','fees'];
    try{
      for(const module of modules){
        const rows=await api(`/api/records/${module}/${currentBranchId}?page=1&page_size=500`);
        if(Array.isArray(rows)) for(const row of rows){if(row?.id!=null){try{await authFetch(`/api/records/${module}/${row.id}`,{method:'DELETE'});}catch(_){}}}
      }
      try{await authFetch(`/api/timetable/all/${currentBranchId}`,{method:'DELETE'});}catch(_){ }
      if(typeof refreshCurrentModule==='function')await refreshCurrentModule();
    }catch(err){console.warn('Initial seeded-data cleanup skipped:',err);}
  }

  /* Remove the obsolete injected exam navigation and keep one sidebar only. */
  const removeLegacyExamButtons=()=>{document.querySelectorAll('[data-exam-v2],[data-exam-extra]').forEach(el=>el.remove());};

  const style=document.createElement('style');
  style.textContent=`
    #moduleSidebar{align-items:stretch!important;position:relative!important;transition:width .18s ease,min-width .18s ease!important}
    #moduleGroups{width:100%;padding:6px 10px 14px!important}
    #moduleGroups>div,#moduleGroups>button{width:100%;margin:0 0 2px!important;box-sizing:border-box!important}
    #moduleGroups .sidebar-item{box-sizing:border-box!important;width:100%!important;margin:0!important;padding-left:16px!important;padding-right:12px!important;min-height:42px!important}
    #moduleGroups .sidebar-item:hover,#moduleGroups .sidebar-item.active{padding-left:16px!important}
    #moduleGroups .module-icon{display:inline-flex!important;width:22px!important;min-width:22px!important;justify-content:center!important;text-align:center!important}
    #sidebarCollapseButton{position:absolute;right:-13px;top:10px;z-index:50;width:27px;height:27px;border:1px solid rgba(212,175,55,.35);border-radius:50%;background:#0b0b0b;color:#e8c767;font-size:20px;line-height:22px;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.5)}
    #sidebarCollapseButton:hover{background:#171717;color:#f4e5a1}
    #moduleSidebar.sidebar-collapsed{width:64px!important;min-width:64px!important}
    #moduleSidebar.sidebar-collapsed #moduleGroups .sidebar-item{justify-content:center!important;padding:11px 0!important}
    #moduleSidebar.sidebar-collapsed #moduleGroups .sidebar-item span:last-child,.sidebar-collapsed .module-head-group>div:first-child>span{display:none!important}
    #moduleSidebar.sidebar-collapsed .module-head-group>div:first-child{justify-content:center!important;padding:6px 0!important}
    #moduleSidebar.sidebar-collapsed .module-head-group .module-head-arrow{padding:4px!important}
    #dashboardWelcomeRow{margin-bottom:10px!important}
    #dashboardClockWidget{text-align:right;min-width:230px;margin-left:auto}
    #dashboardClockWidget .clock-time{font-family:'Fraunces',serif;font-size:1.45rem;font-weight:800;letter-spacing:.01em;line-height:1.1}
    #dashboardClockWidget .clock-date{margin-top:4px;color:#9ca3af;font-size:.7rem;font-weight:700;letter-spacing:.11em;text-transform:uppercase}
    #headerInstituteName{font-size:clamp(2.25rem,4vw,3.6rem)!important;line-height:1!important;max-width:min(58vw,900px)!important}
    #teacherConfigList{max-height:58vh!important;min-height:280px!important;padding-right:8px!important}
    #teacherConfigList>div{padding:14px!important}
    #teacherConfigList input{min-height:38px!important;font-size:.82rem!important;padding:.55rem!important}
    #teacherConfigList .grid{gap:.65rem!important}
    body.sidebar-is-collapsed #mainContent{padding-left:2rem!important}
  `;
  document.head.appendChild(style);

  function bootEnhancements(){
    removeLegacyExamButtons(); renderCanonicalSidebar(); installSidebarCollapse(); installDashboardClock(); clearSeededRecordsOnce();
    setTimeout(()=>{removeLegacyExamButtons();renderCanonicalSidebar();installDashboardClock();},400);
    setTimeout(()=>{removeLegacyExamButtons();renderCanonicalSidebar();installDashboardClock();},1200);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bootEnhancements);else bootEnhancements();
  new MutationObserver(()=>{installDashboardClock();installSidebarCollapse();}).observe(document.body,{childList:true,subtree:true});
})();
