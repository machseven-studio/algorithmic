from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / 'index.html'
MAIN = ROOT / 'main.py'
MARKER = '<!-- IBEX RUNTIME PATCH -->'
SCRIPT = r'''
<!-- IBEX RUNTIME PATCH -->
<style id="ibex-runtime-css">
:root{--ibex-cream:#f4ead7;--ibex-ink:#1b1710;--ibex-gold:#b08a3c}
.ibex-clock{display:inline-flex;flex-direction:column;justify-content:center;gap:2px;margin-left:24px;vertical-align:middle;color:inherit;font-family:inherit;white-space:nowrap;text-align:left}
.ibex-clock-time{font:inherit;font-size:.42em;line-height:1.05;letter-spacing:.04em}.ibex-clock-date{font:inherit;font-size:.18em;line-height:1.2;opacity:.82;letter-spacing:.08em}
.ibex-parallax-search{margin:18px 0 24px;padding:18px 20px;border:1px solid rgba(232,199,103,.24);border-radius:20px;background:linear-gradient(145deg,#12110e,#070707);box-shadow:0 20px 55px rgba(0,0,0,.3)}
.ibex-parallax-search label{display:block;font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:#8f8567;font-weight:800;margin-bottom:9px}.ibex-parallax-row{display:flex;gap:10px}.ibex-parallax-row input{flex:1;min-width:0;background:#050505;border:1px solid rgba(212,175,55,.24);border-radius:12px;padding:13px;color:#eee;outline:none}.ibex-parallax-row button{border:0;border-radius:12px;padding:12px 18px;background:#c6a64b;color:#090909;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
.ibex-timetable-wide{width:calc(100vw - 112px)!important;max-width:none!important;min-height:78vh!important;margin-left:0!important;margin-right:0!important}
#mainContent .ibex-home-ongoing{width:100%!important;min-height:120px!important;height:120px!important;display:flex!important;align-items:center!important}.ibex-hidden-home-stat{display:none!important}
#moduleSidebar.sidebar-collapsed>*{visibility:hidden!important}#moduleSidebar.sidebar-collapsed{overflow:hidden!important}
</style>
<script>
(function(){
'use strict';
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
const text=e=>(e?.textContent||'').replace(/\s+/g,' ').trim();
function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
let initialBranchDone=false;
function forceMainCampus(){
  const sel=$('#branchSelector'); if(!sel) return false;
  const opt=Array.from(sel.options).find(o=>String(o.textContent||'').trim().toLowerCase()==='main campus');
  if(!opt) return false;
  if(!initialBranchDone){initialBranchDone=true;if(window.currentBranchId!==undefined)window.currentBranchId=opt.value;try{localStorage.setItem('currentBranchId',opt.value)}catch(e){}sel.value=opt.value;sel.dispatchEvent(new Event('change',{bubbles:true}));}
  return true;
}
const branchTimer=setInterval(()=>{if(forceMainCampus())clearInterval(branchTimer)},250);
function installClock(){
  const candidates=$$('h1,h2,h3,div,p,span').filter(e=>/^welcome\s+/i.test(text(e))&&e.children.length<6);
  const welcome=candidates.sort((a,b)=>a.textContent.length-b.textContent.length)[0];
  if(!welcome||welcome.querySelector('.ibex-clock')||$('#ibexClock'))return;
  const clock=document.createElement('span');clock.id='ibexClock';clock.className='ibex-clock';const time=document.createElement('span');time.className='ibex-clock-time';const date=document.createElement('span');date.className='ibex-clock-date';clock.append(time,date);welcome.appendChild(clock);
  function tick(){const d=new Date();time.textContent=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true}).format(d)+' IST';date.textContent=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',weekday:'long',day:'2-digit',month:'short',year:'numeric'}).format(d)}tick();setInterval(tick,1000);
}
function trimHomeStats(){
 const main=$('#mainContent');if(!main)return;
 $$('.glass-panel').forEach(card=>{if(!main.contains(card))return;const t=text(card).toLowerCase();if(!t||/parallax|institute search|welcome/.test(t))return;if(/ongoing\s+lectures?/.test(t)){card.classList.add('ibex-home-ongoing');return}if(/total\s+(students?|teachers?|classrooms?)|attendance\s*(rate|today|overview)?|pending\s*(fees|payments?)|fees?\s*(pending|collected|due)|students?\s*(present|absent|enrolled)|analytics|overview|summary/.test(t)&&t.length<900)card.classList.add('ibex-hidden-home-stat')});
}
function installSearch(){
 const main=$('#mainContent');if(!main||$('#ibexParallaxSearch'))return;const welcome=$$('#mainContent h1,#mainContent h2,#mainContent h3').find(e=>/^welcome\s+/i.test(text(e)));const host=welcome?.closest('.glass-panel')||main.querySelector('.glass-panel');if(!host)return;const box=document.createElement('section');box.id='ibexParallaxSearch';box.className='ibex-parallax-search';box.innerHTML='<label>Parallax · Institute Search</label><div class="ibex-parallax-row"><input id="ibexSearchInput" placeholder="Search students, teachers, attendance, fees, classrooms…"><button type="button">Search</button></div><div id="ibexSearchResults"></div>';host.after(box);box.querySelector('button').onclick=runSearch;box.querySelector('input').onkeydown=e=>{if(e.key==='Enter')runSearch()};
}
async function runSearch(){const q=$('#ibexSearchInput')?.value.trim(),out=$('#ibexSearchResults'),bid=window.currentBranchId;if(!q||!out||!bid)return;out.innerHTML='<div class="text-xs text-gray-500 pt-3">Searching institute data…</div>';const mods=['students','teachers','attendance','fees','classrooms','syllabus'];let hits=[];await Promise.all(mods.map(async m=>{try{const r=await(window.authFetch?window.authFetch(`/api/records/${m}/${bid}?search=${encodeURIComponent(q)}&page_size=8`):fetch(`/api/records/${m}/${bid}?search=${encodeURIComponent(q)}&page_size=8`));if(r.ok){const d=await r.json();(Array.isArray(d)?d:[]).slice(0,3).forEach(x=>hits.push({m,x}))}}catch(e){}}));out.innerHTML=hits.length?hits.map(h=>`<button type="button" class="w-full text-left mt-2 p-3 rounded-xl bg-black/30 border border-white/5 hover:border-yellow-700/30"><span class="text-[9px] uppercase tracking-widest text-yellow-700">${escapeHtml(h.m)}</span><div class="text-xs text-gray-200">${escapeHtml(h.x.name||h.x.student_name||h.x.subject||h.x.room_no||'Record')}</div></button>`).join(''):'<div class="text-xs text-gray-500 pt-3">No matching records found in this branch.</div>'}
function removeAttendanceHistory(){const root=$('#mainContent');if(!root)return;$$('button,a,[role="button"]').forEach(el=>{if(/^history$/i.test(text(el))){const section=el.closest('.glass-panel,.module-panel,section,div');if(section&&/attendance/i.test(text(section)))el.remove()}})}
function widenTimetable(){$$('*').forEach(el=>{if(/^generated\s+weekly\s+schedule$/i.test(text(el))){(el.closest('.glass-panel')||el.parentElement)?.classList.add('ibex-timetable-wide')}})}
function removeSidebarSymbols(){$$('#moduleSidebar .sidebar-item').forEach(el=>{const t=text(el).toLowerCase();if(/^(seating|invigilation)$/.test(t))$$('svg,img,i,[class*="icon"]',).forEach(icon=>{if(el.contains(icon))icon.remove()})})}
function normalizeExamHeaders(){const reference=$$('#mainContent h1,#mainContent h2,#mainContent h3').find(e=>{const t=text(e).toLowerCase();return t&&!/results|history/.test(t)});if(!reference)return;const cs=getComputedStyle(reference);$$('h1,h2,h3').filter(e=>/^(results|history)$/i.test(text(e))).forEach(e=>{e.style.fontFamily=cs.fontFamily;e.style.fontSize=cs.fontSize;e.style.fontWeight=cs.fontWeight;e.style.letterSpacing=cs.letterSpacing;e.style.color=cs.color})}
let headGuard=false;function installHeadGuard(){if(headGuard)return;headGuard=true;document.addEventListener('click',ev=>{const head=ev.target.closest?.('.module-head-group > div:first-child,.module-head-title,.module-head-arrow');if(!head)return;const group=head.closest('.module-head-group');if(!group)return;setTimeout(()=>{$$('.module-head-group').forEach(g=>{if(g===group)return;const items=g.querySelector('.module-head-items');if(items)items.style.display='none';const ar=g.querySelector('.module-head-arrow');if(ar)ar.classList.remove('rotate-180')})},0)},true)}
function patchPdf(){const ns=window.jspdf;if(!ns||!ns.jsPDF||ns.jsPDF.__ibexPatched)return;const Original=ns.jsPDF;function IBEXjsPDF(){const doc=new Original(...arguments);doc.__ibexExecutive=true;const W=doc.internal.pageSize.getWidth(),H=doc.internal.pageSize.getHeight();const oldAdd=doc.addPage.bind(doc),oldSave=doc.save.bind(doc);function cream(){doc.saveGraphicsState();doc.setFillColor(244,234,215);doc.rect(0,0,W,H,'F');doc.restoreGraphicsState()}cream();const oldText=doc.text.bind(doc);doc.text=function(txt,x,y,opts){try{doc.setTextColor(27,23,16);doc.setFont('times','normal')}catch(e){}return oldText(txt,x,y,opts)};doc.addPage=function(){const r=oldAdd.apply(null,arguments);cream();return r};if(typeof doc.autoTable==='function'){const oldTable=doc.autoTable.bind(doc);doc.autoTable=function(opts){opts=opts||{};opts.theme=opts.theme||'grid';opts.styles=Object.assign({font:'times',fontSize:9,textColor:[27,23,16],fillColor:[244,234,215],lineColor:[176,138,60],lineWidth:.2},opts.styles||{});opts.headStyles=Object.assign({font:'times',fontStyle:'bold',textColor:[27,23,16],fillColor:[224,210,181]},opts.headStyles||{});return oldTable(opts)}}doc.save=function(){cream();return oldSave.apply(null,arguments)};return doc}IBEXjsPDF.prototype=Original.prototype;Object.setPrototypeOf(IBEXjsPDF,Original);IBEXjsPDF.__ibexPatched=true;ns.jsPDF=IBEXjsPDF}
function brandEverything(){document.title='I.B.E.X. — Institutional Backbone EXecutive';const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);nodes.forEach(n=>{if(/ALGORITHMIC/i.test(n.nodeValue))n.nodeValue=n.nodeValue.replace(/ALGORITHMIC/gi,'I.B.E.X.')});$$('h1,h2,h3,div,span').filter(e=>/^I\.B\.E\.X\.?$/i.test(text(e))&&e.children.length<3).forEach(e=>{if(e.nextElementSibling?.classList.contains('ibex-subtitle'))return;const s=document.createElement('div');s.className='ibex-subtitle';s.innerHTML='<span>Institutional</span><span>Backbone</span><span>EXecutive</span>';s.style.cssText='font-family:Fraunces,serif;font-size:.34em;line-height:1.05;letter-spacing:.08em;opacity:.72;margin-top:2px';e.insertAdjacentElement('afterend',s)})}
function tick(){try{forceMainCampus();installClock();trimHomeStats();installSearch();removeAttendanceHistory();widenTimetable();removeSidebarSymbols();normalizeExamHeaders();installHeadGuard();patchPdf();brandEverything()}catch(e){console.warn('I.B.E.X. UI patch:',e)}}
new MutationObserver(()=>{clearTimeout(window.__ibexMutation);window.__ibexMutation=setTimeout(tick,80)}).observe(document.body,{childList:true,subtree:true});setTimeout(tick,100);setInterval(tick,1500);
})();
</script>
'''

def ensure_index():
    if not INDEX.exists(): return
    s=INDEX.read_text(encoding='utf-8')
    if MARKER not in s:
        s=s.replace('</body>',SCRIPT+'\n</body>',1)
        s=s.replace('<title>ALGORITHMIC - Enterprise Institutional Operations</title>','<title>I.B.E.X. — Institutional Backbone EXecutive</title>')
        INDEX.write_text(s,encoding='utf-8')

def ensure_main():
    if not MAIN.exists(): return
    s=MAIN.read_text(encoding='utf-8')
    s=s.replace('title="ALGORITHMIC"','title="I.B.E.X. — Institutional Backbone EXecutive"').replace('ALGORITHMIC','I.B.E.X.')
    if '/api/branches/default/{institute_id}' not in s:
        marker='\n# ---------------------------------------------------------------------------\n# Frontend\n# ---------------------------------------------------------------------------'
        if marker in s:
            route='''\n@app.get("/api/branches/default/{institute_id}")\ndef default_branch(institute_id: int, institute: CurrentInstitute = Depends(get_current_institute)):\n    if institute.id != institute_id: raise HTTPException(status_code=403, detail="Forbidden")\n    conn=get_conn()\n    try:\n        cur=conn.cursor(); cur.execute("SELECT id,name FROM branches WHERE institute_id=%s AND lower(trim(name))='main campus' ORDER BY id LIMIT 1",(institute_id,)); row=cur.fetchone(); return {"id":row["id"],"name":row["name"]} if row else None\n    finally: conn.close()\n'''
            s=s.replace(marker,route+marker,1)
    MAIN.write_text(s,encoding='utf-8')

ensure_main(); ensure_index()
