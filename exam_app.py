from pathlib import Path
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.routing import APIRoute
import main

# The existing examination backend is kept as the canonical data/API layer.
# Add the new modules to the same Examination permission head before importing
# it because exam_modules calls main.check_module_access at request time.
main.MODULE_HEAD.update({'results':'examination','history':'examination'})
import exam_modules
exam_modules.app = main.app

BASE=Path(__file__).resolve().parent
INDEX=BASE/'index.html'
JS=BASE/'exam_frontend.js'
INJECTION='<script src="/exam_frontend.js?v=results-history-3"></script>'

def root():
    html=INDEX.read_text(encoding='utf-8')
    if '/exam_frontend.js' not in html:
        html=html.replace('</body>',INJECTION+'</body>',1)
    return HTMLResponse(html,headers={'Cache-Control':'no-store'})

for route in list(main.app.router.routes):
    if isinstance(route,APIRoute) and route.path=='/' and 'GET' in route.methods:
        main.app.router.routes.remove(route)
main.app.add_api_route('/',root,methods=['GET'],response_class=HTMLResponse)

@main.app.get('/exam_frontend.js')
def exam_frontend():
    return FileResponse(JS,media_type='application/javascript',headers={'Cache-Control':'no-store'})

app=main.app
