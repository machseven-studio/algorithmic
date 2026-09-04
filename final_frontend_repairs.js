/* ALGORITHMIC FINAL FRONTEND REPAIR
   Loaded by the existing application patch layer when present.
   This layer deliberately does not replace the application's module renderers. */
(function () {
  'use strict';
  const $ = (s, r=document) => r.querySelector(s);
  const $$ = (s, r=document) => [...r.querySelectorAll(s)];
  const clean = v => {
    if (v == null) return '';
    if (typeof v === 'object') return clean(v.full_name ?? v.name ?? v.student_name ?? v.label ?? v.value ?? v.id ?? '');
    return String(v);
  };

  const MODULES = {
    homepage: [
      ['dashboard','⌂','Dashboard'],
      ['attendance','▣','Attendance'],
      ['syllabus','▤','Syllabus'],
      ['timetables','▦','Timetable'],
      ['fees','₹','Fees'],
      ['whatsapp','✉','WhatsApp Messaging']
    ],
    examination: [
      ['seating','♙','Seating'],
      ['invigilation','◈','Invigilation'],
      ['results','◫','Results'],
      ['history','◴','History']
    ],
    administration: [
      ['manage-users','♟','Manage Users'],
      ['analytics','◒','Analytics']
    ]
  };

  function side() { return $('#moduleSidebar'); }
  function groups() { return $('#moduleGroups'); }

  function addCollapseButton() {
    const s = side();
    if (!s || $('#algorithmicSidebarToggle')) return;
    const b = document.createElement('button');
    b.id = 'algorithmicSidebarToggle';
    b.type = 'button';
    b.setAttribute('aria-label','Collapse navigation');
    b.title = 'Collapse navigation';
    b.textContent = '‹';
    Object.assign(b.style, {
      position:'absolute', top:'12px', right:'-13px', zIndex:'100', width:'28px', height:'28px',
      border:'1px solid rgba(212,175,55,.55)', borderRadius:'50%', background:'#090909',
      color:'#e8c767', fontSize:'20px', lineHeight:'24px', cursor:'pointer'
    });
    b.onclick = () => {
      const collapsed = s.classList.toggle('algorithmic-sidebar-collapsed');
      document.body.classList.toggle('algorithmic-sidebar-is-collapsed', collapsed);
      b.textContent = collapsed ? '›' : '‹';
      b.title = collapsed ? 'Open navigation' : 'Collapse navigation';
      b.setAttribute('aria-label', b.title);
    };
    s.appendChild(b);
  }

  function installStyles() {
    if ($('#algorithmicFinalStyles')) return;
    const st=document.createElement('style'); st.id='algorithmicFinalStyles';
    st.textContent=`
      #moduleSidebar { position:relative !important; width:280px; min-width:280px; transition:width .18s ease,min-width .18s ease; overflow:visible !important; }
      #moduleSidebar.algorithmic-sidebar-collapsed { width:0 !important; min-width:0 !important; padding:0 !important; border-width:0 !important; }
      #moduleSidebar.algorithmic-sidebar-collapsed > *:not(#algorithmicSidebarToggle) { visibility:hidden !important; opacity:0 !important; pointer-events:none !important; }
      #moduleSidebar.algorithmic-sidebar-collapsed #algorithmicSidebarToggle { visibility:visible !important; opacity:1 !important; pointer-events:auto !important; right:-30px !important; }
      #moduleGroups { display:flex !important; flex-direction:column !important; align-items:stretch !important; width:100% !important; gap:2px !important; }
      #moduleGroups .sidebar-item { width:100% !important; min-height:42px; display:flex !important; align-items:center !important; }
      #moduleGroups .module-head-group { width:100% !important; }
      #moduleGroups .module-head-group > div:first-child { width:100%; display:flex; align-items:center; justify-content:space-between; }
      #moduleGroups .module-head-arrow { display:inline-flex !important; visibility:visible !important; opacity:1 !important; pointer-events:auto !important; }
      #moduleGroups .module-head-label { pointer-events:none !important; }
      .algorithmic-sidebar-is-collapsed #mainContent { width:100% !important; }
      .algorithmic-sidebar-is-collapsed #moduleSidebar + #mainContent { margin-left:0 !important; }
      .algorithmic-gold-clock { margin-left:auto; text-align:right; }
      .algorithmic-gold-clock .time { font-family:Fraunces,serif; font-size:1.35rem; font-weight:800; }
      .algorithmic-gold-clock .date { color:#9ca3af; font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; margin-top:3px; }
      #teacherConfigList, #ttTimingRows { min-height:240px !important; max-height:58vh !important; }
    `;
    document.head.appendChild(st);
  }

  function findWelcome() {
    return $$('.command-heading-font,h1,h2,h3,div,p').find(e => /^\s*WELCOME\b/i.test(clean(e.textContent).trim()));
  }
  function clock() {
    const w=findWelcome(); if (!w) return;
    let c=$('#algorithmicGoldClock');
    if (!c) {
      const row=w.parentElement;
      if (!row) return;
      row.style.display='flex'; row.style.alignItems='center'; row.style.justifyContent='space-between'; row.style.width='100%'; row.style.gap='24px';
      c=document.createElement('div'); c.id='algorithmicGoldClock'; c.className='algorithmic-gold-clock'; row.appendChild(c);
    }
    const now=new Date();
    const time=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true}).format(now);
    const date=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'2-digit',month:'long',year:'numeric'}).format(now);
    c.innerHTML='<div class="time gold-gradient-text">'+time+' IST</div><div class="date">'+date+'</div>';
  }

  function home() {
    window.currentModule='home';
    if (typeof window.showModule === 'function') return window.showModule('home');
    if (typeof window.switchModule === 'function') return window.switchModule('home');
    if (typeof window.renderDashboard === 'function') return window.renderDashboard();
    if (typeof window.refreshCurrentModule === 'function') return window.refreshCurrentModule();
  }

  function preventBrokenHeadClicks() {
    const g=groups(); if(!g) return;
    $$('.module-head-group',g).forEach(group => {
      const head=group.querySelector(':scope > div:first-child');
      if(!head || head.dataset.finalBound) return;
      head.dataset.finalBound='1';
      const arrow=group.querySelector('.module-head-arrow');
      if(!arrow) return;
      // Only the arrow toggles. The heading itself is not a module navigation action.
      head.addEventListener('click', e => {
        if (e.target !== arrow && !arrow.contains(e.target)) e.stopImmediatePropagation();
      }, true);
      arrow.addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation();
        const target=group.querySelector('[id^="head-"]') || group.querySelector('.module-head-children');
        if(target) target.classList.toggle('hidden');
        arrow.textContent = target && !target.classList.contains('hidden') ? '⌃' : '⌄';
      }, true);
    });
  }

  function restoreHomepageHead() {
    const g=groups(); if(!g) return;
    // If an earlier repair replaced the sidebar, restore the Homepage head and its children.
    let homeHead=$('[data-head-group="homepage"]',g);
    if (!homeHead) {
      const div=document.createElement('div'); div.className='module-head-group'; div.dataset.headGroup='homepage';
      div.innerHTML='<div class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg"><span class="module-head-label text-[10px] font-black uppercase tracking-widest text-gray-400">Homepage</span><button type="button" class="module-head-arrow px-2 py-1 text-gray-400" aria-label="Toggle Homepage">⌃</button></div><div id="head-homepage" class="space-y-0.5"></div>';
      g.prepend(div); homeHead=div;
    }
    const children=$('#head-homepage',homeHead);
    if (children && !children.children.length) {
      children.innerHTML=MODULES.homepage.map(([m,i,l])=>`<button data-module="${m}" class="sidebar-item w-full text-left px-4 py-2.5 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span>${i}</span><span>${l}</span></button>`).join('');
      const dash=children.querySelector('[data-module="dashboard"]');
      dash?.addEventListener('click',home);
      children.querySelectorAll('[data-module]:not([data-module="dashboard"])').forEach(b=>b.addEventListener('click',()=>window.switchModule?.(b.dataset.module,b)));
    }
  }

  function normalizeAttendance() {
    // Fix the common [object Object] display at the DOM boundary even if a legacy renderer remains.
    $$('td,span,div,p').forEach(el=>{
      if (el.children.length || !el.textContent.includes('[object Object]')) return;
      el.textContent=el.textContent.replace(/\[object Object\]/g,'');
    });
  }

  function run() {
    installStyles(); addCollapseButton(); restoreHomepageHead(); preventBrokenHeadClicks(); clock(); normalizeAttendance();
    const n=$('#moduleGroups');
    if(n && !n.dataset.finalHomeBound){
      n.dataset.finalHomeBound='1';
      n.addEventListener('click',e=>{const b=e.target.closest('[data-module="home"]'); if(b){e.preventDefault();e.stopPropagation();home();}},true);
    }
  }
  const mo=new MutationObserver(()=>run());
  function boot(){ run(); mo.observe(document.body,{subtree:true,childList:true}); setInterval(clock,1000); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',boot); else boot();
})();
