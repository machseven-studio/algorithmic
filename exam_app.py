from pathlib import Path
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.routing import APIRoute

import main
from exam_features import router as examination_router

main.app.include_router(examination_router)
BASE = Path(__file__).resolve().parent
INDEX = BASE / 'index.html'
JS = BASE / 'exam_frontend.js'

INJECTION = '''<script src="/exam_frontend.js?v=results-history-2"></script>
<script>
(function(){
  const patch=()=>{
    document.querySelectorAll('[data-module="seating"] span:last-child').forEach(x=>x.textContent='Seating');
    document.querySelectorAll('[data-module="invigilation"] span:last-child').forEach(x=>x.textContent='Invigilation');
  };
  window.addEventListener('load',patch); setInterval(patch,1000);
  window.addHistoryRecord = async function(){
    try{
      const body={subject:document.getElementById('historySubject').value.trim(),topic:document.getElementById('historyTopic').value.trim(),batch_name:document.getElementById('historyBatch').value.trim(),exam_date:document.getElementById('historyDate').value};
      if(!body.subject||!body.topic||!body.batch_name||!body.exam_date) throw Error('Subject, topic, batch and date are required.');
      const r=await authFetch('/api/examination/history/'+currentBranchId,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(d.detail||'Failed to add history record.');
      await loadExamHistory();
    }catch(e){const x=document.getElementById('historyError');if(x)x.textContent=e.message;}
  };
})();
</script>'''

def root():
    html=INDEX.read_text(encoding='utf-8')
    if '/exam_frontend.js' not in html: html=html.replace('</body>',INJECTION+'</body>',1)
    return HTMLResponse(html,headers={'Cache-Control':'no-store'})

for route in list(main.app.router.routes):
    if isinstance(route,APIRoute) and route.path=='/' and 'GET' in route.methods: main.app.router.routes.remove(route)
main.app.add_api_route('/',root,methods=['GET'],response_class=HTMLResponse)

@main.app.get('/exam_frontend.js')
def exam_frontend(): return FileResponse(JS,media_type='application/javascript',headers={'Cache-Control':'no-store'})

app=main.app
