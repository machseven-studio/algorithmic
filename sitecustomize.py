# I.B.E.X. startup patcher. Runs before uvicorn imports main.py.
try:
    import ibex_patch  # noqa: F401
except Exception as exc:
    print(f'I.B.E.X. startup patch warning: {exc}')
