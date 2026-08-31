import os
from flask import Flask, request, redirect, session, flash

app = Flask(__name__)
application = app
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-production')

# -----------------------------------------------------------------------------
# 1. CSS
# -----------------------------------------------------------------------------
CSS = """
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: #f4f4f4; color: #333; }
header { background: #2c3e50; color: white; padding: 1rem 2rem; display: flex; justify-content: space-between; align-items: center; }
nav a { color: white; text-decoration: none; margin-left: 15px; font-weight: bold; }
nav a:hover { color: #f1c40f; }
.container { max-width: 1200px; margin: 20px auto; padding: 20px; background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
h1 { color: #2c3e50; }
.btn { padding: 10px 20px; background: #27ae60; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn:hover { background: #219150; }
.table { width: 100%; border-collapse: collapse; margin-top: 10px; }
.table th, .table td { border: 1px solid #ddd; padding: 8px; text-align: left; }
.table th { background-color: #f2f2f2; }
.status-paid { color: green; font-weight: bold; }
.status-unpaid { color: red; font-weight: bold; }
.dashboard-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
.card { background: #ecf0f1; padding: 20px; border-radius: 8px; text-align: center; }
.card h3 { margin: 0; font-size: 2rem; }
.card p { margin: 5px 0 0; color: #7f8c8d; }
footer { text-align: center; padding: 20px; color: #7f8c8d; font-size: 0.9rem; }
.modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); justify-content: center; align-items: center; }
.modal-content { background: white; padding: 20px; border-radius: 8px; width: 50%; max-width: 500px; }
.close-btn { float: right; cursor: pointer; font-size: 1.5rem; }
"""

# -----------------------------------------------------------------------------
# 2. LAYOUT & CONTENT (Using .replace() to inject Jinja2 logic)
# -----------------------------------------------------------------------------

# Base Layout
BASE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title if title else 'School Admin' }}</title>
    <style>{{ CSS }}</style>
</head>
<body>
    <header>
        <div class="logo">SCHOOL ADMIN</div>
        <nav>
            <a href="/">Home</a>
            {{ NAV }}
        </nav>
    </header>
    <div class="container">
        {{ ALERT }}
        {{ CONTENT }}
    </div>
    <footer>
        <a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | 
        <a href="/terms" style="color: #7f8c8d;">Terms of Service</a>
    </footer>
</body>
</html>"""

NAV_LOGGED = """<a href="/dashboard">Dashboard</a> <a href="/seating">Seating</a> <a href="/audit">Audit</a> <a href="/logout">Logout</a>"""
NAV_GUEST = """<a href="/login">Login</a>"""
ALERT = """{% with messages = get_flashed_messages() %}{% if messages %}<div style="background: #ffebee; color: #c62828; padding: 10px; margin-bottom: 15px; border-radius: 4px;">{% for message in messages %}<p>{{ message }}</p>{% endfor %}</div>{% endif %}{% endwith %}"""

# Pages
PAGE_HOME = """<div style="text-align: center; padding: 50px 0;">
    <h1>Welcome to the School Admin Panel</h1>
    <p>Manage students, fees, and seating with ease.</p>
    {% if not session.get('logged_in') %}
        <a href="/login"><button class="btn">Admin Login</button></a>
    {% else %}
        <a href="/dashboard"><button class="btn">Go to Dashboard</button></a>
    {% endif %}
</div>"""

PAGE_LOGIN = """<div style="max-width: 400px; margin: 40px auto;">
    <h2>Admin Login</h2>
    <form method="POST" action="/login">
        <label>Username</label>
        <input type="text" name="username" required style="width: 100%; padding: 8px; margin-bottom: 10px;">
        <label>Password</label>
        <input type="password" name="password" required style="width: 100%; padding: 8px; margin-bottom: 20px;">
        <button type="submit" class="btn" style="width: 100%;">Login</button>
    </form>
</div>"""

PAGE_DASHBOARD = """<h1>Dashboard Overview</h1>
<div class="dashboard-grid">
    <div class="card"><h3>1,240</h3><p>Total Students</p></div>
    <div class="card"><h3>$45,000</h3><p>Fees Collected</p></div>
    <div class="card"><h3>98%</h3><p>Attendance Rate</p></div>
</div>"""

PAGE_SEATING = """<h1>Student Seating</h1>
<button class="btn" onclick="openModal()">Assign Seat</button>
<table class="table">
    <thead><tr><th>ID</th><th>Name</th><th>Seat No</th><th>Status</th></tr></thead>
    <tbody>
        <tr><td>001</td><td>John Doe</td><td>A-12</td><td class="status-paid">Active</td></tr>
        <tr><td>002</td><td>Jane Smith</td><td
