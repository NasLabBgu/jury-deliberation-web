from flask import Flask, render_template, request, jsonify, Response
import os
import tempfile
import shutil
import json
import subprocess
import threading
import queue
import time
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create temporary directory for uploads
TEMP_DIR = tempfile.mkdtemp(prefix="jury_uploads_")
JUROR_DIR = os.path.join(TEMP_DIR, "jurors")
CASE_DIR = os.path.join(TEMP_DIR, "cases")

# Create subdirectories
os.makedirs(JUROR_DIR, exist_ok=True)
os.makedirs(CASE_DIR, exist_ok=True)

print(f"Upload directory: {TEMP_DIR}")

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_files():
    try:
        # Check if the request contains files
        if 'files' not in request.files:
            return jsonify({'error': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            return jsonify({'error': 'No files selected'}), 400
        
        # Get additional data about categories and weights
        categories = request.form.getlist('categories')
        weights = request.form.getlist('weights')
        
        # Clear existing files first
        print("Clearing existing files...")
        
        # Clear juror directory
        if os.path.exists(JUROR_DIR):
            for filename in os.listdir(JUROR_DIR):
                filepath = os.path.join(JUROR_DIR, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)
                    print(f"Deleted: {filepath}")
        
        # Clear case directory
        if os.path.exists(CASE_DIR):
            for filename in os.listdir(CASE_DIR):
                filepath = os.path.join(CASE_DIR, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)
                    print(f"Deleted: {filepath}")
        
        print("All existing files cleared.")
        
        results = []
        
        for i, file in enumerate(files):
            if file.filename != '':
                filename = secure_filename(file.filename)
                category = categories[i] if i < len(categories) else 'juror'
                weight = int(weights[i]) if i < len(weights) and weights[i].isdigit() else 100
                
                # Determine target directory
                target_dir = JUROR_DIR if category == 'juror' else CASE_DIR
                
                # Save the actual file
                filepath = os.path.join(target_dir, filename)
                file.save(filepath)
                
                print(f"Uploaded: {filepath} (category: {category}, weight: {weight})")
                
                results.append({
                    'filename': filename,
                    'category': category,
                    'weight': weight,
                    'path': filepath,
                    'status': 'uploaded'
                })
        
        return jsonify({
            'success': True,
            'uploaded_files': results,
            'temp_dir': TEMP_DIR,
            'message': f'Cleared existing files and uploaded {len(results)} new files'
        })
        
    except Exception as e:
        print(f"Upload error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route("/run", methods=["POST"])
def run_process():
    try:
        data = request.get_json()
        
        # Get parameters
        juror_count = data.get('juror_count', 5)
        repeat_count = data.get('repeat_count', 3)
        evaluation_options = data.get('evaluation_options', [])
        
        # List uploaded files
        juror_files = os.listdir(JUROR_DIR) if os.path.exists(JUROR_DIR) else []
        case_files = os.listdir(CASE_DIR) if os.path.exists(CASE_DIR) else []
        
        # Check if we have required files
        if not juror_files:
            return jsonify({'error': 'No juror files uploaded'}), 400
        if not case_files:
            return jsonify({'error': 'No case files uploaded'}), 400
        
        # Select first available files (you can modify this logic)
        jury_file = juror_files[0]
        case_file = case_files[0]
        
        result = {
            'success': True,
            'message': f'Process started with {juror_count} jurors, {repeat_count} repetitions',
            'juror_files': juror_files,
            'case_files': case_files,
            'evaluation_options': evaluation_options,
            'temp_dir': TEMP_DIR,
            'selected_jury_file': jury_file,
            'selected_case_file': case_file
        }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/run_notebook", methods=["GET"])
def run_notebook():
    """Execute the Jupyter notebook with uploaded files and stream results"""
    try:
        # Get parameters from query string
        total_rounds = int(request.args.get('repeat_count', 3))
        
        # List uploaded files
        juror_files = os.listdir(JUROR_DIR) if os.path.exists(JUROR_DIR) else []
        case_files = os.listdir(CASE_DIR) if os.path.exists(CASE_DIR) else []
        
        # Check if we have required files
        if not juror_files:
            def error_gen():
                yield f"data: {json.dumps({'status': 'error', 'message': 'No juror files uploaded'})}\n\n"
            return Response(error_gen(), mimetype='text/event-stream')
            
        if not case_files:
            def error_gen():
                yield f"data: {json.dumps({'status': 'error', 'message': 'No case files uploaded'})}\n\n"
            return Response(error_gen(), mimetype='text/event-stream')
        
        # Select first available files
        jury_file_name = juror_files[0]
        case_file_name = case_files[0]
        
        def generate():
            """Generator function to stream notebook execution results"""
            try:
                # Change to backend directory
                backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
                
                yield f"data: {json.dumps({'status': 'started', 'message': f'Running deliberation with {jury_file_name} and {case_file_name}'})}\n\n"
                
                # Create a custom Python script that imports from the notebook and calls run_deliberation
                jury_file_path = os.path.join(JUROR_DIR, jury_file_name)
                case_file_path = os.path.join(CASE_DIR, case_file_name)
                
                script_content = f'''
import sys
import os
import subprocess

# Install required packages
print("Installing required packages...")
packages = [
    "langgraph", 
    "langchain-openai", 
    "langchain-core", 
    "langchain-google-genai", 
    "pyyaml"
]

for package in packages:
    try:
        print(f"Installing {{package}}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"✓ {{package}} installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install {{package}}: {{e}}")

print("Package installation completed\\n")

sys.path.append('{backend_dir}')

# Change to backend directory to access notebook functions
os.chdir('{backend_dir}')

print(f"Using jury file: {jury_file_path}")
print(f"Using case file: {case_file_path}")

# Import and execute the notebook cells
try:
    # Execute notebook to set up all functions and variables
    print("Loading notebook...")
    exec(open('run_notebook_functions.py').read())
    
    print("Starting deliberation...")
    # Debug: Print the actual paths being passed
    print(f"DEBUG - About to call run_deliberation with:")
    print(f"  jury_file='{jury_file_path}'")
    print(f"  case_file='{case_file_path}'")
    print(f"  scenario_number=1")
    print(f"  total_rounds={total_rounds}")
    
    # Call run_deliberation with uploaded files from temp directories
    run_deliberation(
        jury_file="{jury_file_path}",
        case_file="{case_file_path}",
        scenario_number=1,
        total_rounds={total_rounds},
        save_to_file=True
    )
    print("Deliberation completed successfully!")
    
except Exception as e:
    print(f"Error during deliberation: {{e}}")
    import traceback
    traceback.print_exc()
'''
                
                # Create a Python file with the notebook functions
                notebook_functions_file = os.path.join(backend_dir, 'run_notebook_functions.py')
                yield f"data: {json.dumps({'status': 'output', 'message': 'Extracting notebook functions...'})}\n\n"
                
                # Extract Python code from the notebook
                import json as py_json
                with open(os.path.join(backend_dir, 'langgraph_jury_deliberation.ipynb'), 'r') as f:
                    notebook = py_json.load(f)
                
                python_code = []
                for cell in notebook['cells']:
                    if cell['cell_type'] == 'code':
                        cell_source = ''.join(cell['source'])
                        # Filter out notebook-specific commands
                        lines = cell_source.split('\n')
                        filtered_lines = []
                        for line in lines:
                            # Skip shell commands (starting with !) and empty lines
                            if not line.strip().startswith('!') and line.strip():
                                filtered_lines.append(line)
                        
                        if filtered_lines:  # Only add if there's actual Python code
                            python_code.append('\n'.join(filtered_lines))
                
                # Write extracted code to a file
                with open(notebook_functions_file, 'w') as f:
                    f.write('\n\n'.join(python_code))
                
                yield f"data: {json.dumps({'status': 'output', 'message': 'Notebook functions extracted successfully'})}\n\n"
                
                # Write the script to a temporary file
                script_file = os.path.join(backend_dir, 'temp_deliberation_script.py')
                with open(script_file, 'w') as f:
                    f.write(script_content)
                
                yield f"data: {json.dumps({'status': 'output', 'message': 'Script created, starting execution...'})}\n\n"
                
                # Execute the script and capture output
                process = subprocess.Popen(
                    ['python', 'temp_deliberation_script.py'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    cwd=backend_dir
                )
                
                # Stream output line by line
                for line in iter(process.stdout.readline, ''):
                    if line:
                        yield f"data: {json.dumps({'status': 'output', 'message': line.strip()})}\n\n"
                
                # Wait for process to complete
                process.wait()
                
                # Clean up temporary files
                try:
                    os.remove(script_file)
                    os.remove(notebook_functions_file)
                except:
                    pass
                
                if process.returncode == 0:
                    yield f"data: {json.dumps({'status': 'completed', 'message': 'Deliberation completed successfully'})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'Deliberation failed with code {process.returncode}'})}\n\n"
                        
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': f'General error: {str(e)}'})}\n\n"
        
        return Response(generate(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*'
        })
        
    except Exception as e:
        def error_gen():
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream')

if __name__ == "__main__":
    try:
        app.run(debug=True, port=5001, host="127.0.0.1")
    finally:
        # Cleanup temp directory on exit
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)