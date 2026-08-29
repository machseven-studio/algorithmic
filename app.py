import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Classroom Dynamics API")

@app.get("/")
def root():
    return {"status": "ok", "message": "EduOps Automator is running"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/deploy-check")
def deploy_check():
    return {
        "python_version": "3.12.4",
        "dependencies_installed": True,
        "timestamp": "2026-08-29T13:15:00Z"
    }
