from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- DATABASE SETUP ---
def get_db():
    db_path = 'classroom.db'
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student'
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            due_date TEXT,
            created_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER,
            student_id INTEGER,
            content TEXT,
            FOREIGN KEY (assignment_id) REFERENCES assignments(id),
            FOREIGN KEY (student_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- LOGIN STATE CHECK ---
# <--- THIS IS LIKELY THE PROBLEM IF YOU'RE CHECKING SESSION HERE
if 'user_id' in session:
    user_id = session['user_id']
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        session['username'] = user['username']
        session['role'] = user['role']
    conn.close()
# --------------------------------------------------------

@app.route('/')
def home():
    return render_template('home.html', username=session.get('username'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password)).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    if user['role'] == 'teacher':
        assignments = conn.execute('SELECT * FROM assignments').fetchall()
        return render_template('teacher_dashboard.html', assignments=assignments, username=session['username'])
    else:
        assignments = conn.execute('''
            SELECT assignments.*, submissions.content as submission 
            FROM assignments 
            LEFT JOIN submissions ON assignments.id = submissions.assignment_id AND submissions.student_id = ?
        ''', (session['user_id'],)).fetchall()
        return render_template('student_dashboard.html', assignments=assignments, username=session['username'])

@app.route('/create_assignment', methods=['GET', 'POST'])
def create_assignment():
    if 'role' not in session or session['role'] != 'teacher':
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form.get('description', '')
        due_date = request.form['due_date']
        conn = get_db()
        conn.execute('INSERT INTO assignments (title, description, due_date, created_by) VALUES (?, ?, ?, ?)',
                     (title, description, due_date, session['user_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
    return render_template('create_assignment.html')

@app.route('/submit/<int:assignment_id>', methods=['GET', 'POST'])
def submit_assignment(assignment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        content = request.form['content']
        conn = get_db()
        conn.execute('INSERT INTO submissions (assignment_id, student_id, content) VALUES (?, ?, ?)',
                     (assignment_id, session['user_id'], content))
        conn.commit()
        conn.close()
        return redirect(url_for('dashboard'))
    return render_template('submit.html', assignment_id=assignment_id)

if __name__ == '__main__':
    app.run(debug=True)
