(() => {
  'use strict';
  const escv = v => { if (typeof esc === 'function') return esc(v == null ? '' : String(v)); return String(v ?? '').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])); };
  const scalar = v => {
    if (v == null) return '';
    if (['string','number','boolean'].includes(typeof v)) return String(v);
    if (Array.isArray(v)) return v.map(scalar).join(', ');
    if (typeof v === 'object') return scalar(v.name ?? v.full_name ?? v.student_name ?? v.label ?? v.value ?? v.text ?? v.id ?? '');
    return String(v);
  };

  // ---------- Indian clock ----------
  function paintClock() {
    const el = document.getElementById('dashboardClockWidget');
    if (!el) return;
    const now = new Date();
    const time = new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true}).format(now);
    const date = new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'2-digit',month:'long',year:'numeric'}).format(now);
    el.innerHTML = `<div class="clock-time gold-gradient-text">${escv(time)} IST</div><div class="clock-date">${escv(date)}</div>`;
  }
  function installClock() {
    if (!document.getElementById('dashboardClockWidget')) {
      const nodes = [...document.querySelectorAll('h1,h2,h3,p,div')].filter(x => /^\s*WELCOME\b/i.test((x.textContent||'').trim()));
      const welcome = nodes.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length)[0];
      if (welcome && welcome.parentElement) {
        const row=document.createElement('div'); row.id='dashboardWelcomeRow';
        row.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;width:100%;';
        welcome.parentElement.insertBefore(row,welcome); row.appendChild(welcome);
        const clock=document.createElement('div'); clock.id='dashboardClockWidget'; row.appendChild(clock);
      }
    }
    paintClock();
  }

  // ---------- Canonical sidebar / home ----------
  const canonical = {
    administrations:[['attendance','📋','Attendance'],['syllabus','📚','Syllabus'],['timetables','🗓️','Timetable'],['fees','₹','Fees'],['whatsapp','✉️','WhatsApp Messaging']],
    examination:[['seating','🪑','Seating'],['invigilation','🛡️','Invigilation'],['results','📊','Results'],['history','🗂️','History']]
  };
  function owner(){ return !!window.isOwner; }
  function allowed(h){ return owner() || (Array.isArray(window.myAllowedModules) && window.myAllowedModules.includes(h)); }
  function redrawSidebar(){
    const groups=document.getElementById('moduleGroups'); if(!groups) return;
    const current=window.currentModule || 'home';
    const head = window.MODULE_HEAD?.[current] || (current==='results'||current==='history'?'examination':'homepage');
    const home=allowed('homepage');
    let html=home?`<button data-module="home" id="homeDashboardButton" class="sidebar-item w-full text-left px-4 py-3 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>⌂</span><span>Homepage</span></button>`:'';
    for(const h of ['administrations','examination']){
      if(!allowed(h)) continue;
      const open=h===head;
      html+=`<div class="module-head-group" data-head-group="${h}"><div class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg"><span class="text-[10px] font-black uppercase tracking-widest text-gray-400">${h==='administrations'?'Administrations':'Examination'}</span><button type="button" class="module-head-arrow px-2 py-1 text-gray-400 hover:text-yellow-500" data-head="${h}" aria-label="Toggle ${h}">${open?'⌃':'⌄'}</button></div><div id="head-${h}" class="${open?'':'hidden'} space-y-0.5">${canonical[h].map(([m,i,l])=>`<button data-module="${m}" class="sidebar-item w-full text-left px-4 py-2.5 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>${i}</span><span>${l}</span></button>`).join('')}</div></div>`;
    }
    groups.innerHTML=html;
    groups.querySelector('#homeDashboardButton')?.addEventListener('click',()=>goHome());
    groups.querySelectorAll('.module-head-arrow').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation(); const el=document.getElementById('head-'+b.dataset.head); if(el) el.classList.toggle('hidden');}));
    groups.querySelectorAll('[data-module]:not(#homeDashboardButton)').forEach(b=>b.addEventListener('click',()=>{ if(typeof window.switchModule==='function') window.switchModule(b.dataset.module,b); }));
    groups.querySelector(`[data-module="${current}"]`)?.classList.add('active');
  }
  async function goHome(){
    window.currentModule='home';
    try { if(typeof window.refreshCurrentModule==='function') await window.refreshCurrentModule(); } catch(e) {}
    redrawSidebar();
  }
  window.goHomeDashboard=goHome;

  // ---------- Attendance history: always use the server's canonical endpoint ----------
  window.openAttendanceHistory = async function(ref){
    const name=scalar(ref);
    const modal=document.getElementById('attendanceHistoryModal');
    const body=document.getElementById('attendanceHistoryBody');
    const title=document.getElementById('attendanceHistoryTitle');
    if(title) title.textContent=`Attendance History · ${name}`;
    if(body) body.innerHTML='<p class="text-xs text-gray-500 p-4">Loading attendance history…</p>';
    modal?.classList.remove('hidden');
    try {
      const url=`/api/attendance/history/${window.currentBranchId}?student_name=${encodeURIComponent(name)}`;
      const r=await window.authFetch(url);
      let d={}; try{d=await r.json();}catch(_){d={};}
      if(!r.ok) throw new Error(scalar(d.detail)||`Request failed (${r.status})`);
      const rows=Array.isArray(d)?d:(Array.isArray(d.history)?d.history:[]);
      if(!rows.length){body.innerHTML='<p class="text-xs text-gray-500 p-4">No attendance records found for this student.</p>';return;}
      const present=Number(d.present_count ?? rows.filter(x=>scalar(x.status).toLowerCase()==='present').length);
      const absent=Number(d.absent_count ?? rows.filter(x=>scalar(x.status).toLowerCase()==='absent').length);
      body.innerHTML=`<div class="flex flex-wrap gap-5 text-xs text-gray-400 px-1 pb-3 border-b gold-border mb-3"><span>${rows.length} days marked</span><span class="text-green-400">${present} present</span><span class="text-red-400">${absent} absent</span></div><div class="max-h-96 overflow-y-auto divide-y divide-gray-900">${rows.map(x=>{const st=scalar(x.status), ok=st.toLowerCase()==='present'; return `<div class="flex justify-between items-center py-3 text-sm"><span class="text-gray-300">${escv(scalar(x.date))}</span><span class="font-semibold ${ok?'text-green-400':'text-red-400'}">${escv(st)}</span></div>`}).join('')}</div>`;
    }catch(e){ if(body) body.innerHTML=`<p class="text-xs text-red-400 p-4">${escv(e.message||'Failed to load attendance history.')}</p>`; }
  };

  // ---------- Collapse button ----------
  function installCollapse(){
    const side=document.getElementById('moduleSidebar'); if(!side||document.getElementById('sidebarCollapseButton')) return;
    const b=document.createElement('button'); b.id='sidebarCollapseButton'; b.type='button'; b.textContent='‹'; b.title='Collapse navigation'; b.setAttribute('aria-label','Collapse navigation'); side.appendChild(b);
    b.onclick=()=>{const c=side.classList.toggle('sidebar-collapsed'); b.textContent=c?'›':'‹'; b.title=c?'Open navigation':'Collapse navigation';};
  }

  // Remove old standalone controls without removing canonical Examination children.
  function removeLegacy(){
    const g=document.getElementById('moduleGroups'); if(!g)return;
    g.querySelectorAll(':scope > button, :scope > a').forEach(x=>{const t=(x.textContent||'').trim().toLowerCase(); if(t==='history'||t==='seating')x.remove();});
  }

  // Make timetable selection area substantially larger after every module render.
  function enlargeTimetable(){
    ['teacherConfigList','ttTimingRows'].forEach(id=>{const e=document.getElementById(id);if(e){e.style.minHeight='220px';e.style.maxHeight='55vh';e.style.overflowY='auto';}});
    document.querySelectorAll('#moduleContent .glass-panel').forEach(p=>{if(/teacher|constraint|timing/i.test(p.textContent||'')){p.style.minHeight='360px';}});
  }

  function boot(){ installClock(); installCollapse(); redrawSidebar(); removeLegacy(); enlargeTimetable(); }
  const mo=new MutationObserver(()=>{ installClock(); installCollapse(); removeLegacy(); enlargeTimetable(); });
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot); else boot();
  mo.observe(document.body,{childList:true,subtree:true});
  setInterval(paintClock,1000);
})();
