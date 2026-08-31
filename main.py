import os
from flask import Flask, request, redirect, session, render_template_string

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-me'

# --- CSS STYLES (Your UI) ---
STYLES = """
<style>
body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; background: #f4f4f4; color: #333; }
header { background: #2c3e50; color: white; padding: 1rem 2rem; display: flex; justify-content: space-between; align-items: center; }
nav a { color: white; text-decoration: none; margin-left: 15px; font-weight: bold; }
nav a:hover { color: #f1c40f; }
.container { max-width: 1200px; margin: 20px auto; padding: 20px; background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
h1 { color: #2c3e50; }
.btn { padding: 10px 20px; background: #27ae60; color: white; border: none; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; }
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
</style>
"""

# --- TEMPLATES ---
HOME_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Home</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/">Home</a>{'<a href="/login">Login</a>' if not session.get('logged_in') else '<a href="/dashboard">Dashboard</a>'}</nav></header>
    <div class="container"><div style="text-align: center; padding: 50px 0;"><h1>Welcome to the School Admin Panel</h1><p>Manage students, fees, and seating with ease.</p>{'<a href="/login" class="btn">Admin Login</a>' if not session.get('logged_in') else '<a href="/dashboard" class="btn">Go to Dashboard</a>'}</div></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

LOGIN_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Login</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/">Home</a></nav></header>
    <div class="container" style="max-width: 400px; margin: 40px auto;"><h2>Admin Login</h2><form method="POST" action="/login"><label>Username</label><input type="text" name="username" required style="width: 100%; padding: 8px; margin-bottom: 10px;"><label>Password</label><input type="password" name="password" required style="width: 100%; padding: 8px; margin-bottom: 20px;"><button type="submit" class="btn" style="width: 100%;">Login</button></form></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

DASHBOARD_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Dashboard</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/dashboard">Dashboard</a><a href="/seating">Seating</a><a href="/fees">Fees</a><a href="/audit">Audit</a><a href="/logout">Logout</a></nav></header>
    <div class="container"><h1>Dashboard Overview</h1><div class="dashboard-grid"><div class="card"><h3>1,240</h3><p>Total Students</p></div><div class="card"><h3>$45,000</h3><p>Fees Collected</p></div><div class="card"><h3>98%</h3><p>Attendance Rate</p></div></div></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

SEATING_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Seating</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/dashboard">Dashboard</a><a href="/seating">Seating</a><a href="/fees">Fees</a><a href="/audit">Audit</a><a href="/logout">Logout</a></nav></header>
    <div class="container"><h1>Student Seating</h1><button class="btn" onclick="document.getElementById('seatModal').style.display='flex'">Assign Seat</button><table class="table"><thead><tr><th>ID</th><th>Name</th><th>Seat No</th><th>Status</th></tr></thead><tbody><tr><td>001</td><td>John Doe</td><td>A-12</td><td class="status-paid">Active</td></tr><tr><td>002</td><td>Jane Smith</td><td>B-04</td><td class="status-unpaid">Pending</td></tr></tbody></table>
    <div id="seatModal" class="modal"><div class="modal-content"><span class="close-btn" onclick="document.getElementById('seatModal').style.display='none'">&times;</span><h3>Assign Seat</h3><form><input type="text" placeholder="Student ID" style="width: 100%; padding: 8px; margin-bottom: 10px;"><input type="text" placeholder="Seat Number" style="width: 100%; padding: 8px; margin-bottom: 10px;"><button type="button" class="btn" onclick="document.getElementById('seatModal').style.display='none'">Save</button></form></div></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

FEES_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Fees</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/dashboard">Dashboard</a><a href="/seating">Seating</a><a href="/fees">Fees</a><a href="/audit">Audit</a><a href="/logout">Logout</a></nav></header>
    <div class="container"><h1>Fee Management</h1><table class="table"><thead><tr><th>Student</th><th>Amount Due</th><th>Status</th><th>Action</th></tr></thead><tbody><tr><td>John Doe</td><td>$500</td><td class="status-unpaid">Unpaid</td><td><button class="btn" style="padding: 5px 10px; font-size: 0.8rem;">Mark Paid</button></td></tr></tbody></table></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

AUDIT_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Audit</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/dashboard">Dashboard</a><a href="/seating">Seating</a><a href="/fees">Fees</a><a href="/audit">Audit</a><a href="/logout">Logout</a></nav></header>
    <div class="container"><h1>Audit Logs</h1><table class="table"><thead><tr><th>Time</th><th>Action</th><th>User</th></tr></thead><tbody><tr><td>2023-10-25 10:00</td><td>Updated Fee Status</td><td>admin</td></tr><tr><td>2023-10-24 14:30</td><td>Added New Student</td><td>admin</td></tr></tbody></table></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

PRIVACY_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Privacy</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/">Home</a></nav></header>
    <div class="container"><h1>Privacy Policy</h1><p>We collect your data to manage school records securely.</p></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

TERMS_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Terms</title>{STYLES}</head>
<body>
    <header><div class="logo">SCHOOL ADMIN</div><nav><a href="/">Home</a></nav></header>
    <div class="container"><h1>Terms of Service</h1><p>By using this panel, you agree to the terms of use.</p></div>
    <footer><a href="/privacy" style="color: #7f8c8d;">Privacy Policy</a> | <a href="/terms" style="color: #7f8c8d;">Terms of Service</a></footer>
</body>
</html>
"""

# --- ROUTES ---

@app.route('/')
def home():
    return HOME_HTML

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'admin':
            session['logged_in'] = True
            return redirect('/dashboard')
    return LOGIN_HTML

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'): return redirect('/login')
    return DASHBOARD_HTML

@app.route('/seating')
def seating():
    if not session.get('logged_in'): return redirect('/login')
    return SEATING_HTML

@app.route('/fees')
def fees():
    if not session.get('logged_in'): return redirect('/login')
    return FEES_HTML

@app.route('/audit')
def audit():
    if not session.get('logged_in'): return redirect('/login')
    return AUDIT_HTML

@app.route('/privacy')
def privacy():
    return PRIVACY_HTML

@app.route('/terms')
def terms():
    return TERMS_HTML

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
