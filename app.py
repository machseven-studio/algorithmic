import os
import sqlite3
import magic
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import bcrypt
from werkzeug.utils import secure_filename

app = Flask(__name__)

# FIX 1: Allow web browsers to talk to this server safely (CORS)
CORS(app)

# Allowed file types (checks internal file structure, not just the file name extension)
ALLOWED_MIME_TYPES = {'application/pdf', 'image/jpeg', 'image/png'}

def get_db():
    conn = sqlite3.connect('app.db')
    conn.row_factory = sqlite3.Row
    return conn

# Database Setup
with get_db() as conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS modules (id INTEGER PRIMARY KEY, name TEXT)''')

# FIX 2: Modern Bcrypt Password Security
def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

# FIX 3: Secure File Uploads (Inspects file content bytes)
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file selected'}), 400
    
    file = request.files['file']
    filename = secure_filename(file.filename)
    
    # Read first 2048 bytes to identify the true file type
    header = file.read(2048)
    file.seek(0)
    
    file_type = magic.from_buffer(header, mime=True)
    if file_type not in ALLOWED_MIME_TYPES:
        return jsonify({'error': 'Security rejected: File content is invalid or unsafe'}), 400
        
    os.makedirs('uploads', exist_ok=True)
    file.save(os.path.join('uploads', filename))
    return jsonify({'message': 'File uploaded securely'}), 200

# FIX 4: Secure Database Query (Prevents SQL Injection attacks)
@app.route('/delete-module', methods=['POST'])
def delete_module():
    data = request.get_json() or {}
    module_name = data.get('name')
    
    if not module_name:
        return jsonify({'error': 'Module name required'}), 400
        
    with get_db() as conn:
        # The '?' keeps database commands safely separated from user text
        conn.execute("DELETE FROM modules WHERE name = ?", (module_name,))
        conn.commit()
        
    return jsonify({'message': f'Module {module_name} safely removed'}), 200

# FIX 5: Single HTML Frontend Delivery
@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
