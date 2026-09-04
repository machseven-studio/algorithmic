# Render/Python startup safety net for the authentication frontend.
# Python imports sitecustomize automatically when this repository is on sys.path.
# The repair is idempotent and runs before uvicorn imports main.py.
try:
    import repair_auth  # noqa: F401
except Exception as exc:
    print(f"Algorithmic auth repair warning: {exc}")
