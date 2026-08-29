# app.py
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# Check if DB URL exists to prevent startup crash
DB_URL = os.getenv("DATABASE_URL")

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def read_root():
    return "Hello World - If you see this, the server is up!"

# If you have other imports that might fail, wrap them in a try/except block
try:
    from routes import router
    app.include_router(router)
except Exception as e:
    print(f"Warning: Could not load routes: {e}")
