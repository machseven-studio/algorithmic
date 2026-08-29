from flask import Flask, render_template_string, request, redirect, url_for, flash, send_file
import openpyxl
import os
import uuid

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Define the templates
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Algorithmic - Intelligent Automation</title>
    <!-- Fonts: Space Grotesk for headings, Inter for body -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
    
    <style>
        :root {
            --bg-color: #000000;
            --text-color: #ffffff;
            --accent-color: #00ff9d; /* Neon Green for contrast */
            --card-bg: #111111;
            --border-color: #333333;
        }

        body {
            margin: 0;
            padding: 0;
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Inter', sans-serif;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow-x: hidden;
        }

        /* Heavy Texture Overlay */
        body::before {
            content: "";
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.08'/%3E%3C/svg%3E");
            pointer-events: none;
            z-index: -1;
        }

        .container {
            max-width: 600px;
            margin: 0 auto;
            padding: 40px 20px;
            width: 100%;
            box-sizing: border-box;
        }

        h1, h2, h3, .brand {
            font-family: 'Space Grotesk', sans-serif;
            font-weight: 700;
            letter-spacing: -0.5px;
        }

        .brand {
            font-size: 2rem;
            margin-bottom: 10px;
            color: var(--text-color);
            text-transform: uppercase;
        }

        .subtitle {
            font-family: 'Inter', sans-serif;
            font-weight: 400;
            color: #888;
            margin-bottom: 40px;
            font-size: 0.95rem;
        }

        /* Upload Box Styles */
        .upload-box {
            border: 2px dashed var(--border-color);
            border-radius: 12px;
            padding: 60px 20px;
            text-align: center;
            transition: all 0.3s ease;
            background: var(--card-bg);
            cursor: pointer;
            position: relative;
        }

        .upload-box.dragover {
            border-color: var(--accent-color);
            background: #0a0a0a;
            transform: scale(1.02);
        }

        .upload-box:hover {
            border-color: #555;
        }

        .upload-icon {
            font-size: 3rem;
            margin-bottom: 20px;
            color: var(--accent-color);
        }

        .upload-text {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 1.2rem;
            font-weight: 500;
            margin-bottom: 10px;
        }

        .upload-hint {
            font-size: 0.85rem;
            color: #666;
        }

        /* File Input Hidden */
        input[type="file"] {
            display: none;
        }

        /* Buttons */
        .btn {
            display: inline-block;
            background-color: var(--text-color);
            color: var(--bg-color);
            padding: 15px 30px;
            font-family: 'Space Grotesk', sans-serif;
            font-weight: 700;
            text-transform: uppercase;
            font-size: 0.9rem;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            transition: transform 0.2s ease, opacity 0.2s ease;
            text-decoration: none;
            margin-top: 20px;
        }

        .btn:hover {
            transform: translateY(-2px);
            opacity: 0.9;
        }

        .btn-secondary {
            background-color: transparent;
            color: var(--text-color);
            border: 1px solid var(--border-color);
        }

        .btn-secondary:hover {
            border-color: var(--text-color);
        }

        /* Results Section */
        .results-container {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 30px;
            margin-top: 20px;
        }

        .result-item {
            display: flex;
            justify-content: space-between;
            padding: 15px 0;
            border-bottom: 1px solid var(--border-color);
            font-family: 'Inter', sans-serif;
        }

        .result-item:last-child {
            border-bottom: none;
        }

        .result-label {
            color: #888;
            font-weight: 600;
        }

        .result-value {
            font-weight: 700;
            color: var(--text-color);
        }

        .error-msg {
            color: #ff4d4d;
            margin-top: 10px;
            font-size: 0.9rem;
        }

        .success-msg {
            color: var(--accent-color);
            margin-top: 10px;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="brand">Algorithmic</h1>
        <p class="subtitle">Automated Clerical Intelligence</p>

        {% if error %}
            <div class="error-msg">{{ error }}</div>
        {% endif %}

        {% if result %}
            <div class="results-container">
                <h2>Analysis Complete</h2>
                {% for key, value in result.items() %}
                <div class="result-item">
                    <span class="result-label">{{ key }}</span>
                    <span class="result-value">{{ value }}</span>
                </div>
                {% endfor %}
                <div style="text-align: center;">
                    <a href="/" class="btn btn-secondary">Upload Another</a>
                    <a href="/download" class="btn">Download Result</a>
                </div>
            </div>
        {% else %}
            <form action="/" method="post" enctype="multipart/form-data" id="uploadForm">
                <div class="upload-box" id="dropZone">
                    <div class="upload-icon">📂</div>
                    <div class="upload-text">Drop Excel File Here</div>
                    <div class="upload-hint">or click to browse (.xlsx, .xls)</div>
                    <input type="file" name="file" id="fileInput" accept=".xlsx,.xls">
                </div>
                <div style="text-align: center;">
                    <button type="submit" class="btn" id="submitBtn" disabled>Process File</button>
                </div>
            </form>
        {% endif %}
    </div>

    <script>
        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const submitBtn = document.getElementById('submitBtn');
        const uploadForm = document.getElementById('uploadForm');

        // Helper to highlight drop zone
        function highlight(e) {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.add('dragover');
        }

        function unhighlight(e) {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove('dragover');
        }

        // Handle File Drop
        dropZone.addEventListener('dragover', highlight, false);
        dropZone.addEventListener('dragleave', unhighlight, false);
        dropZone.addEventListener('drop', function(e) {
            unhighlight(e);
            const dt = e.dataTransfer;
            const files = dt.files;
            handleFiles(files);
        }, false);

        // Handle Click to Browse
        dropZone.addEventListener('click', function() {
            fileInput.click();
        });

        fileInput.addEventListener('change', function() {
            handleFiles(this.files);
        });

        function handleFiles(files) {
            if (files.length > 0) {
                const file = files[0];
                const validTypes = ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
                                    'application/vnd.ms-excel'];
                const validExtensions = ['.xlsx', '.xls'];
                
                const isValidExtension = validExtensions.some(ext => file.name.toLowerCase().endsWith(ext));
                
                if (isValidExtension || validTypes.includes(file.type)) {
                    fileInput.files = files; // Set the file input value
                    submitBtn.disabled = false;
                    submitBtn.textContent = "Process " + file.name;
                } else {
                    alert("Please upload a valid Excel file (.xlsx or .xls)");
                }
            }
        }
    </script>
</body>
</html>
"""

# Simple logic to just read the first cell or row count to prove it works
# In a real scenario, you'd parse the specific columns for your algorithm
HTML_RESULT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Algorithmic - Result</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #000000;
            --text-color: #ffffff;
            --accent-color: #00ff9d;
            --card-bg: #111111;
            --border-color: #333333;
        }
        body {
            margin: 0;
            padding: 0;
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Inter', sans-serif;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow-x: hidden;
        }
        body::before {
            content: "";
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)' opacity='0.08'/%3E%3C/svg%3E");
            pointer-events: none;
            z-index: -1;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            padding: 40px 20px;
            width: 100%;
            box-sizing: border-box;
        }
        h1, h2, h3 {
            font-family: 'Space Grotesk', sans-serif;
            font-weight: 700;
        }
        .brand {
            font-size: 2rem;
            margin-bottom: 10px;
            color: var(--text-color);
            text-transform: uppercase;
        }
        .results-container {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 30px;
            margin-top: 20px;
        }
        .result-item {
            display: flex;
            justify-content: space-between;
            padding: 15px 0;
            border-bottom: 1px solid var(--border-color);
            font-family: 'Inter', sans-serif;
        }
        .result-item:last-child {
            border-bottom: none;
        }
        .result-label {
            color: #888;
            font-weight: 600;
        }
        .result-value {
            font-weight: 700;
            color: var(--text-color);
        }
        .btn {
            display: inline-block;
            background-color: var(--text-color);
            color: var(--bg-color);
            padding: 15px 30px;
            font-family: 'Space Grotesk', sans-serif;
            font-weight: 700;
            text-transform: uppercase;
            font-size: 0.9rem;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            transition: transform 0.2s ease, opacity 0.2s ease;
            text-decoration: none;
            margin-top: 20px;
        }
        .btn:hover {
            transform: translateY(-2px);
            opacity: 0.9;
        }
        .btn-secondary {
            background-color: transparent;
            color: var(--text-color);
            border: 1px solid var(--border-color);
        }
        .btn-secondary:hover {
            border-color: var(--text-color);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="brand">Algorithmic</h1>
        <div class="results-container">
            <h2>File Processed</h2>
            {% for key, value in result.items() %}
            <div class="result-item">
                <span class="result-label">{{ key }}</span>
                <span class="result-value">{{ value }}</span>
            </div>
            {% endfor %}
            <div style="text-align: center;">
                <a href="/" class="btn btn-secondary">Upload Another</a>
                <a href="/download" class="btn">Download Result</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

# Store the processed file path temporarily
current_file_path = None

@app.route('/', methods=['GET', 'POST'])
def index():
    global current_file_path
    result = None
    error = None

    if request.method == 'POST':
        if 'file' not in request.files:
            error = "No file part in the request."
        else:
            file = request.files['file']
            if file.filename == '':
                error = "No file selected."
            else:
                try:
                    # Save the file temporarily
                    filename = f"{uuid.uuid4().hex}.xlsx"
                    filepath = os.path.join('uploads', filename)
                    os.makedirs('uploads', exist_ok=True)
                    file.save(filepath)
                    current_file_path = filepath

                    # Process the file (Basic Example: Read first sheet and count rows)
                    wb = openpyxl.load_workbook(filepath)
                    ws = wb.active
                    rows = list(ws.iter_rows(values_only=True))
                    row_count = len(rows)
                    
                    # Mock result data
                    result = {
                        "Rows Processed": row_count,
                        "Columns Found": ws.max_column if ws.max_column > 0 else 0,
                        "Status": "Success"
                    }
                    
                except Exception as e:
                    error = f"Error processing file: {str(e)}"

    return render_template_string(HTML_TEMPLATE, result=result, error=error)

@app.route('/download')
def download():
    if current_file_path and os.path.exists(current_file_path):
        return send_file(current_file_path, as_attachment=True)
    return "No file available for download.", 404

if __name__ == '__main__':
    app.run(debug=True)
