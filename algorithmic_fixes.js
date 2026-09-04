/* Algorithmic stability + UI consistency fixes */
(() => {
  'use strict';

  const escValue = (v) => typeof esc === 'function'
    ? esc(v ?? '')
    : String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const valueText = (v) => {
    if (v == null) return '';
    if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
    if (typeof v === 'object') return valueText(v.date ?? v.value ?? v.status ?? v.name ?? v.label ?? v.text ?? v.id ?? JSON.stringify(v));
    return String(v);
  };

  const api = async (url, options = {}) => {
    const res = await authFetch(url, options);
    return readApiJson(res, 'Algorithmic request');
  };

  // -----------------------------------------------------------------------
  // Parallax: the original handler could leave the button disabled forever
  // when the Gemini request stalled. Abort locally and always unlock the UI.
  // -----------------------------------------------------------------------
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
      const res = await authFetch(`/api/assistant/${currentBranchId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
        signal: controller.signal
      });
      const data = await readApiJson(res, 'Parallax');
      turn.answer = valueText(data.answer) || '(no answer returned)';
    } catch (err) {
      turn.answer = err?.name === 'AbortError'
        ? 'Parallax timed out. The AI service took too long to respond; your session is still active, so you can ask again.'
        : `Couldn’t get an answer: ${valueText(err?.message || 'unknown error')}`;
    } finally {
      clearTimeout(timer);
      turn.pending = false;
      if (btn) btn.disabled = false;
      if (typeof renderParallaxThread === 'function') renderParallaxThread();
    }
  };

  // -----------------------------------------------------------------------
  // Centralised analytics: Chart.js is treated as an optional CDN dependency
  // and loaded through window.Chart, eliminating the bare-global failure.
  // -----------------------------------------------------------------------
  let chartLoader = null;
  const ensureChartJs = () => {
    if (window.Chart) return Promise.resolve(window.Chart);
    if (chartLoader) return chartLoader;
    chartLoader = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js';
      script.onload = () => window.Chart ? resolve(window.Chart) : reject(new Error('Chart.js loaded without a Chart global.'));
      script.onerror = () => reject(new Error('Chart.js could not be loaded. Refresh and retry analytics.'));
      document.head.appendChild(script);
    });
    return chartLoader;
  };

  window.renderCentralAnalyticsContent = async function (d) {
    const el = document.getElementById('centralAnalyticsContent');
    if (!el) return;
    try {
      await ensureChartJs();
      const a = d?.attendance || {}, f = d?.fees || {};
      const trend = Array.isArray(a.trend) ? a.trend : [];
      const batches = Array.isArray(a.by_batch) ? a.by_batch : [];
      const revenue = Array.isArray(f.revenue) ? f.revenue.slice().reverse() : [];
      (window.__centralAnalyticsCharts || []).forEach(c => { try { c.destroy(); } catch (_) {} });
      window.__centralAnalyticsCharts = [];
      el.innerHTML = `
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          ${centralStatCard('Students', d.students_total)}
          ${centralStatCard('Teachers', d.teachers_total)}
          ${centralStatCard('Classrooms', d.classrooms_total)}
          ${centralStatCard('Fees Pending', '₹' + Number(f.pending_amount || 0).toLocaleString('en-IN'))}
        </div>
        <div class="grid md:grid-cols-2 gap-6">
          <div class="glass-panel border gold-border rounded-2xl p-6"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Attendance — Last 7 Days</div><canvas id="centralAttendanceChart" height="220"></canvas></div>
          <div class="glass-panel border gold-border rounded-2xl p-6"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Attendance by Batch</div><canvas id="centralBatchChart" height="220"></canvas></div>
          <div class="glass-panel border gold-border rounded-2xl p-6 md:col-span-2"><div class="text-xs uppercase tracking-widest text-gray-400 font-bold mb-4">Fee Revenue (Recent)</div><canvas id="centralRevenueChart" height="180"></canvas></div>
        </div>`;
      const C = window.Chart;
      const scale = { x:{ticks:{color:'#9ca3af'},grid:{color:'rgba(212,175,55,.12)'}}, y:{ticks:{color:'#9ca3af'},grid:{color:'rgba(212,175,55,.12)'}} };
      const ac = document.getElementById('centralAttendanceChart');
      if (ac) window.__centralAnalyticsCharts.push(new C(ac, {type:'line',data:{labels:trend.map(x=>valueText(x.label)),datasets:[{label:'Present %',data:trend.map(x=>Number(x.pct)||0),borderColor:'#d4af37',backgroundColor:'rgba(212,175,55,.15)',tension:.35,fill:true}]},options:{plugins:{legend:{labels:{color:'#9ca3af'}}},scales:{...scale,y:{...scale.y,min:0,max:100}}}}));
      const bc = document.getElementById('centralBatchChart');
      if (bc) window.__centralAnalyticsCharts.push(new C(bc, {type:'bar',data:{labels:batches.map(x=>valueText(x.batch)),datasets:[{label:'Attendance %',data:batches.map(x=>Number(x.pct)||0),backgroundColor:'#d4af37'}]},options:{plugins:{legend:{display:false}},scales:{...scale,y:{...scale.y,min:0,max:100}}}}));
      const rc = document.getElementById('centralRevenueChart');
      if (rc) window.__centralAnalyticsCharts.push(new C(rc, {type:'bar',data:{labels:revenue.map(x=>valueText(x.date)),datasets:[{label:'Revenue (₹)',data:revenue.map(x=>Number(x.amount)||0),backgroundColor:'rgba(212,175,55,.65)'}]},options:{plugins:{legend:{display:false}},scales:scale}}));
    } catch (err) {
      el.innerHTML = `<div class="glass-panel border border-red-900/40 p-8 rounded-2xl text-center"><div class="text-red-400 font-bold mb-2">Analytics charts could not be loaded</div><div class="text-xs text-gray-500">${escValue(err.message || 'Chart.js unavailable')}</div></div>`;
    }
  };

  // -----------------------------------------------------------------------
  // Attendance history: normalize object-shaped values and render a clean
  // table. This also gives a useful error instead of the generic [object Object].
  // -----------------------------------------------------------------------
  window.openAttendanceHistory = async function (studentName) {
    const modal = document.getElementById('attendanceHistoryModal');
    const body = document.getElementById('attendanceHistoryBody');
    const title = document.getElementById('attendanceHistoryTitle');
    if (title) title.textContent = `Attendance History · ${valueText(studentName)}`;
    if (body) body.innerHTML = '<p class="text-xs text-gray-500 p-4">Loading…</p>';
    modal?.classList.remove('hidden');
    try {
      const data = await api(`/api/attendance/history/${currentBranchId}?student_name=${encodeURIComponent(valueText(studentName))}`);
      const rows = Array.isArray(data.history) ? data.history : [];
      if (!rows.length) { if (body) body.innerHTML = '<p class="text-xs text-gray-500 p-4">No attendance marked yet for this student.</p>'; return; }
      if (body) body.innerHTML = `
        <div class="flex justify-between text-xs text-gray-400 px-1 pb-3 border-b gold-border mb-3">
          <span>${Number(data.total_marked) || rows.length} days marked</span>
          <span class="text-green-400">${Number(data.present_count) || 0} present</span>
          <span class="text-red-400">${Number(data.absent_count) || 0} absent</span>
        </div>
        <div class="max-h-72 overflow-y-auto divide-y divide-gray-900">
          ${rows.map(h => { const date=valueText(h?.date), status=valueText(h?.status), present=status.toLowerCase()==='present'; return `<div class="flex justify-between items-center py-2 text-sm"><span class="text-gray-300">${escValue(date)}</span><span class="font-semibold ${present?'text-green-400':'text-red-400'}">${escValue(status)}</span></div>`; }).join('')}
        </div>`;
    } catch (err) {
      if (body) body.innerHTML = `<p class="text-xs text-red-400 p-4">${escValue(err.message || 'Failed to load attendance history.')}</p>`;
    }
  };

  // -----------------------------------------------------------------------
  // One canonical sidebar. Results + History are first-class Examination
  // children, not late-injected buttons with a second visual language.
  // -----------------------------------------------------------------------
  const renderCanonicalSidebar = () => {
    const groups = document.getElementById('moduleGroups');
    if (!groups) return;
    const modules = {
      homepage:[['analytics','◈','Analytics'],['assistant','✦','Parallax'],['students','🎓','Student Department'],['teachers','👨‍🏫','Teacher Department'],['classrooms','🏛️','Classroom Department'],['users','🔐','Manage Users']],
      administrations:[['attendance','📋','Attendance'],['syllabus','📚','Syllabus'],['timetables','🕒','Timetable'],['fees','💳','Fees'],['whatsapp','💬','WhatsApp Messaging']],
      examination:[['seating','🪑','Seating'],['invigilation','🛡️','Invigilation'],['results','▣','Results'],['history','◷','History']]
    };
    const heads = ['homepage','administrations','examination'].filter(h => isOwner || (myAllowedModules || []).includes(h));
    groups.innerHTML = heads.map((head, i) => {
      const children = modules[head].filter(([m]) => m !== 'users' || isOwner);
      const open = head === (MODULE_HEAD[currentModule] || currentModule) || (i === 0 && currentModule === 'home');
      return `<div class="mb-1"><button id="head-btn-${head}" onclick="toggleModuleHead('${head}')" class="w-full flex items-center justify-between px-3 py-3 rounded-lg text-[10px] font-black uppercase tracking-widest text-gray-400 hover:text-yellow-500 hover:bg-[#111]"><span>${head==='homepage'?'Homepage':head==='administrations'?'Administrations':'Examination'}</span><span class="head-chevron ${open?'rotate-180':''}">⌄</span></button><div id="head-${head}" class="${open?'':'hidden'} space-y-0.5">${children.map(([m,icon,label])=>`<button data-module="${m}" onclick="switchModule('${m}', this)" class="sidebar-item w-full text-left px-6 py-2.5 text-xs font-extrabold uppercase text-gray-300 flex items-center space-x-3"><span class="module-icon">${icon}</span><span>${label}</span></button>`).join('')}</div></div>`;
    }).join('');
    const active = groups.querySelector(`[data-module="${currentModule}"]`);
    if (active) active.classList.add('active');
  };
  window.renderModuleGroups = renderCanonicalSidebar;

  // The existing exam upgrade scripts override switchModule. Put our routing last.
  const legacySwitch = window.switchModule;
  window.switchModule = async function(moduleName, clickedButton = null) {
    if (moduleName === 'results' || moduleName === 'history') {
      if (!isOwner && !(myAllowedModules || []).includes('examination')) return alert('Your account does not have access to that module.');
      currentModule = moduleName;
      document.querySelectorAll('.sidebar-item').forEach(b => b.classList.remove('active'));
      clickedButton?.classList.add('active');
      if (moduleName === 'results' && typeof window.openResults === 'function') return window.openResults();
      if (moduleName === 'history' && typeof window.openHistory === 'function') return window.openHistory();
    }
    return legacySwitch.call(this, moduleName, clickedButton);
  };

  // Keep legacy observers from adding duplicate navigation entries.
  const removeLegacyExamButtons = () => {
    document.querySelectorAll('[data-exam-v2],[data-exam-extra]').forEach(el => el.remove());
  };
  setTimeout(() => { removeLegacyExamButtons(); renderCanonicalSidebar(); }, 0);
  setTimeout(() => { removeLegacyExamButtons(); renderCanonicalSidebar(); }, 300);
  setTimeout(() => { removeLegacyExamButtons(); renderCanonicalSidebar(); }, 1000);

  const css = document.createElement('style');
  css.textContent = `
    #moduleSidebar{align-items:stretch!important}
    #moduleGroups{width:100%;padding:0 10px!important}
    #moduleGroups>div{width:100%;margin:0 0 2px!important}
    #moduleGroups .sidebar-item{box-sizing:border-box!important;width:100%!important;margin:0!important;padding-left:16px!important;padding-right:12px!important}
    #moduleGroups .sidebar-item:hover,#moduleGroups .sidebar-item.active{padding-left:16px!important}
    #moduleGroups .module-icon{display:inline-flex!important;width:20px!important;min-width:20px!important;justify-content:center!important;text-align:center!important}
  `;
  document.head.appendChild(css);
})();
