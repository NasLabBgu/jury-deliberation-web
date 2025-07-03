import os
import tempfile
import shutil
import json
import subprocess
import threading
import queue
import time
import logging
import pty
import select
import termios
import fcntl
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
try:
    os.makedirs('logs', exist_ok=True)
    log_handler = logging.FileHandler('logs/app.log')
except:
    log_handler = logging.StreamHandler()

logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for more verbose logging
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        log_handler
    ]
)
logger = logging.getLogger(__name__)

# Add early startup logging
logger.info("Starting Flask application initialization...")
logger.info(f"Python path: {os.getcwd()}")
logger.info(f"Environment variables: PORT={os.environ.get('PORT')}, HOST={os.environ.get('HOST')}")

app = Flask(__name__)
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   logger=True,
                   engineio_logger=False,
                   async_mode='eventlet',  # Explicitly use eventlet for async support
                   ping_timeout=60,
                   ping_interval=25)
logger.info("Flask app instance created successfully")

# Production-safe configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

# Environment configuration
PORT = int(os.environ.get('PORT', 8080))  # Cloud Run sets PORT automatically
HOST = os.environ.get('HOST', '0.0.0.0')
DEBUG = os.environ.get('FLASK_ENV') == 'development'

# Create temporary directory for uploads
TEMP_DIR = tempfile.mkdtemp(prefix="jury_uploads_")
JUROR_DIR = os.path.join(TEMP_DIR, "jurors")
CASE_DIR = os.path.join(TEMP_DIR, "cases")

# Create subdirectories
os.makedirs(JUROR_DIR, exist_ok=True)
os.makedirs(CASE_DIR, exist_ok=True)

print(f"Upload directory: {TEMP_DIR}")
logger.info(f"Upload directory: {TEMP_DIR}")

# Global variable to track running processes for termination
current_running_processes = []
process_lock = threading.Lock()

@app.route("/", methods=["GET"])
def index():
    """Main page"""
    try:
        return render_template("index.html")
    except Exception as e:
        logger.error(f"Error serving index page: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for container orchestration"""
    return "OK", 200

@app.route("/upload", methods=["POST"])
def upload_files():
    try:
        # Get additional data about categories and weights
        categories = request.form.getlist('categories')
        weights = request.form.getlist('weights')
        all_files_metadata_str = request.form.get('allFilesMetadata', '[]')
        
        try:
            all_files_metadata = json.loads(all_files_metadata_str)
        except:
            all_files_metadata = []
        
        # Clear existing files first, but preserve generated files that are still referenced
        logger.info("Clearing existing files...")
        
        # Get list of generated files that should be preserved
        preserved_generated_files = [f['name'] for f in all_files_metadata if f.get('generated', False)]
        logger.info(f"Preserving generated files: {preserved_generated_files}")
        
        # Clear file metadata for non-preserved files
        current_metadata = get_all_file_metadata()
        for filename in list(current_metadata.keys()):
            if filename not in preserved_generated_files:
                FILE_METADATA.pop(filename, None)
        
        # Clear juror directory (except preserved generated files)
        if os.path.exists(JUROR_DIR):
            for filename in os.listdir(JUROR_DIR):
                if filename not in preserved_generated_files:
                    filepath = os.path.join(JUROR_DIR, filename)
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        logger.info(f"Deleted: {filepath}")
                else:
                    logger.info(f"Preserved generated file: {filename}")
        
        # Clear case directory (except preserved generated files)
        if os.path.exists(CASE_DIR):
            for filename in os.listdir(CASE_DIR):
                if filename not in preserved_generated_files:
                    filepath = os.path.join(CASE_DIR, filename)
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        logger.info(f"Deleted: {filepath}")
                else:
                    logger.info(f"Preserved generated file: {filename}")
        
        logger.info("All existing files cleared (except preserved generated files).")
        
        # Check if the request contains files
        files = request.files.getlist('files') if 'files' in request.files else []
        
        # Validate file types for security
        allowed_extensions = {'.yaml', '.yml', '.txt'}
        for file in files:
            if file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext not in allowed_extensions:
                    logger.warning(f"Rejected file with invalid extension: {file.filename}")
                    return jsonify({'error': f'Invalid file type: {ext}. Only .yaml, .yml, and .txt files are allowed.'}), 400
        
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
                
                # Store file metadata
                store_file_metadata(filename, category, weight)
                
                logger.info(f"Uploaded: {filepath} (category: {category}, weight: {weight})")
                
                results.append({
                    'filename': filename,
                    'category': category,
                    'weight': weight,
                    'path': filepath,
                    'status': 'uploaded'
                })
        
        # Add preserved generated files to results
        for file_metadata in all_files_metadata:
            if file_metadata.get('generated', False):
                filename = file_metadata['name']
                category = file_metadata['category']
                
                # Check if file exists in the appropriate directory
                target_dir = JUROR_DIR if category == 'juror' else CASE_DIR
                filepath = os.path.join(target_dir, filename)
                
                if os.path.exists(filepath):
                    # Store metadata for preserved generated files
                    store_file_metadata(filename, category, file_metadata['weight'], generated=True)
                    
                    results.append({
                        'filename': filename,
                        'category': category,
                        'weight': file_metadata['weight'],
                        'path': filepath,
                        'status': 'preserved_generated'
                    })
                    logger.info(f"Preserved generated file: {filepath} (category: {category})")
        
        total_files = len(results)
        uploaded_count = len([r for r in results if r['status'] == 'uploaded'])
        preserved_count = len([r for r in results if r['status'] == 'preserved_generated'])
        
        message = f'Uploaded {uploaded_count} new files'
        if preserved_count > 0:
            message += f' and preserved {preserved_count} generated files'
        
        return jsonify({
            'success': True,
            'uploaded_files': results,
            'temp_dir': TEMP_DIR,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

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

@app.route("/stop_process", methods=["POST"])
def stop_process():
    """Stop the currently running notebook execution"""
    try:
        with process_lock:
            if current_running_processes:
                logger.info(f"Stopping {len(current_running_processes)} running processes")
                for process in current_running_processes:
                    try:
                        if process.poll() is None:  # Process is still running
                            process.terminate()
                            # Give it a moment to terminate gracefully
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                # Force kill if it doesn't terminate gracefully
                                process.kill()
                                process.wait()
                            logger.info(f"Process {process.pid} terminated")
                    except Exception as e:
                        logger.error(f"Error terminating process: {e}")
                
                current_running_processes.clear()
                return jsonify({'success': True, 'message': 'All processes stopped'})
            else:
                return jsonify({'success': True, 'message': 'No processes running'})
                
    except Exception as e:
        logger.error(f"Error stopping processes: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/run_notebook", methods=["GET"])
def run_notebook():
    """Execute the Jupyter notebook with uploaded files and stream results"""
    try:
        # Get parameters from query string
        total_rounds = int(request.args.get('repeat_count', 3))
        repeat_mode = request.args.get('repeat_mode', 'individual')
        
        # Get file metadata from the upload results (stored in session or temp file)
        # For now, we'll read the files and their categories from the directories
        juror_files_info = []
        case_files_info = []
        
        # Read juror files with metadata
        if os.path.exists(JUROR_DIR):
            for filename in os.listdir(JUROR_DIR):
                filepath = os.path.join(JUROR_DIR, filename)
                if os.path.isfile(filepath):
                    metadata = get_file_metadata(filename)
                    if metadata['category'] == 'juror':  # Only include files categorized as juror
                        juror_files_info.append({
                            'name': filename,
                            'path': filepath,
                            'weight': metadata['weight']
                        })
        
        # Read case files with metadata
        if os.path.exists(CASE_DIR):
            for filename in os.listdir(CASE_DIR):
                filepath = os.path.join(CASE_DIR, filename)
                if os.path.isfile(filepath):
                    metadata = get_file_metadata(filename)
                    if metadata['category'] == 'case':  # Only include files categorized as case
                        case_files_info.append({
                            'name': filename,
                            'path': filepath,
                            'weight': metadata['weight']
                        })
        
        # Check if we have required files
        if not juror_files_info:
            def error_gen():
                yield f"data: {json.dumps({'status': 'error', 'message': 'No juror files uploaded'})}\n\n"
            return Response(error_gen(), mimetype='text/event-stream')
            
        if not case_files_info:
            def error_gen():
                yield f"data: {json.dumps({'status': 'error', 'message': 'No case files uploaded'})}\n\n"
            return Response(error_gen(), mimetype='text/event-stream')
        
        # Generate the execution pairs based on repeat mode
        def generate_execution_pairs():
            """Generate juror-case pairs based on repeat mode"""
            if repeat_mode == 'individual':
                # Run each unique combination once, ignoring weights
                pairs = []
                for juror_file in juror_files_info:
                    for case_file in case_files_info:
                        pairs.append({
                            'juror_file': juror_file,
                            'case_file': case_file,
                            'run_number': len(pairs) + 1
                        })
                return pairs
            
            else:  # overall mode
                # Use weights to determine frequency of each file
                import random
                
                # Create weighted lists
                weighted_juror_list = []
                for juror_file in juror_files_info:
                    weighted_juror_list.extend([juror_file] * juror_file['weight'])
                
                weighted_case_list = []
                for case_file in case_files_info:
                    weighted_case_list.extend([case_file] * case_file['weight'])
                
                # Generate pairs while trying to maintain uniqueness when possible
                pairs = []
                used_combinations = set()
                
                for run_num in range(1, total_rounds + 1):
                    # Try to find a unique combination first
                    attempts = 0
                    max_attempts = 50
                    
                    while attempts < max_attempts:
                        juror_file = random.choice(weighted_juror_list)
                        case_file = random.choice(weighted_case_list)
                        combo_key = (juror_file['name'], case_file['name'])
                        
                        if combo_key not in used_combinations:
                            used_combinations.add(combo_key)
                            break
                        attempts += 1
                    
                    # If we couldn't find a unique combination, use the last selected pair
                    pairs.append({
                        'juror_file': juror_file,
                        'case_file': case_file,
                        'run_number': run_num
                    })
                
                return pairs
        
        execution_pairs = generate_execution_pairs()
        
        def generate():
            """Generator function to stream notebook execution results"""
            try:
                # Change to backend directory
                backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
                
                total_pairs = len(execution_pairs)
                yield f"data: {json.dumps({'status': 'started', 'message': f'Starting {total_pairs} deliberation runs in {repeat_mode} mode'})}\n\n"
                
                # Log the execution plan
                if repeat_mode == 'individual':
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Running each unique juror-case combination once ({total_pairs} total combinations)'})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Running {total_rounds} total deliberations with weighted selection'})}\n\n"
                
                # Execute each pair
                for i, pair in enumerate(execution_pairs):
                    juror_file = pair['juror_file']
                    case_file = pair['case_file']
                    run_number = pair['run_number']
                    
                    run_header = f'\n=== Run {run_number}/{total_pairs} ==='
                    yield f"data: {json.dumps({'status': 'output', 'message': run_header})}\n\n"
                    juror_name = juror_file['name']
                    case_name = case_file['name']
                    
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Juror file: {juror_name}'})}\n\n"
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Case file: {case_name}'})}\n\n"
                    
                    # Create a custom Python script that imports from the notebook and calls run_deliberation
                    jury_file_path = juror_file['path']
                    case_file_path = case_file['path']
                    
                    script_content = f'''
import sys
import os

# Setup paths and environment
sys.path.append('{backend_dir}')

# Change to backend directory to access notebook functions
os.chdir('{backend_dir}')

print(f"Run {run_number}: Using jury file: {jury_file_path}")
print(f"Run {run_number}: Using case file: {case_file_path}")

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
    print(f"  total_rounds=1")  # Each execution runs only 1 round
    
    # Call run_deliberation with uploaded files from temp directories
    run_deliberation(
        jury_file="{jury_file_path}",
        case_file="{case_file_path}",
        scenario_number=1,
        total_rounds=1,  # Each pair runs once
        save_to_file=True
    )
    print(f"Run {run_number} completed successfully!")
    
except Exception as e:
    print(f"Error during deliberation run {run_number}: {{e}}")
    import traceback
    traceback.print_exc()
'''
                    
                    # Create a Python file with the notebook functions (only once)
                    notebook_functions_file = os.path.join(backend_dir, 'run_notebook_functions.py')
                    if i == 0:  # Only create on first run
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
                    script_file = os.path.join(backend_dir, f'temp_deliberation_script_run_{run_number}.py')
                    with open(script_file, 'w') as f:
                        f.write(script_content)
                    
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Executing run {run_number}...'})}\n\n"
                    
                    # Execute the script and capture output
                    process = subprocess.Popen(
                        ['python', f'temp_deliberation_script_run_{run_number}.py'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        universal_newlines=True,
                        cwd=backend_dir
                    )
                    
                    # Register the process for potential termination
                    with process_lock:
                        current_running_processes.append(process)
                    
                    try:
                        # Stream output line by line
                        for line in iter(process.stdout.readline, ''):
                            if line:
                                yield f"data: {json.dumps({'status': 'output', 'message': line.strip()})}\n\n"
                        
                        # Wait for process to complete
                        process.wait()
                        
                    finally:
                        # Always unregister the process when done
                        with process_lock:
                            if process in current_running_processes:
                                current_running_processes.remove(process)
                    
                    # Clean up temporary script file
                    try:
                        os.remove(script_file)
                    except:
                        pass
                    
                    if process.returncode == 0:
                        yield f"data: {json.dumps({'status': 'output', 'message': f'Run {run_number} completed successfully'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'status': 'error', 'message': f'Run {run_number} failed with code {process.returncode}'})}\n\n"
                        # Continue with next runs even if one fails
                
                # Clean up notebook functions file
                try:
                    os.remove(notebook_functions_file)
                except:
                    pass
                
                yield f"data: {json.dumps({'status': 'completed', 'message': f'All {total_pairs} deliberation runs completed'})}\n\n"
                        
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': f'General error: {str(e)}'})}\n\n"
        
        return Response(generate(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'X-Accel-Buffering': 'no'
        })
        
    except Exception as e:
        def error_gen():
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'X-Accel-Buffering': 'no'
        })
        
    except Exception as e:
        def error_gen():
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'X-Accel-Buffering': 'no'
        })

@app.route("/generate_jurors", methods=["GET"])
def generate_jurors():
    """Generate jurors using NLPAgentsToolbox and stream the output"""
    try:
        juror_count = request.args.get('count', 5, type=int)
        logger.info(f"Starting juror generation for {juror_count} jurors")
        
        def generate():
            try:
                logger.info("Generator function started")
                # Get the backend directory path (where NLPAgentsToolbox should be)
                backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
                nlp_toolbox_dir = os.path.join(backend_dir, 'NLPAgentsToolbox')
                system_python = '/usr/local/bin/python3'  # Docker container Python path
                mkbio_script = os.path.join(nlp_toolbox_dir, 'tools', 'mkbio.py')
                lsbio_script = os.path.join(nlp_toolbox_dir, 'tools', 'lsbio.py')
                
                yield f"data: {json.dumps({'status': 'started', 'message': f'Starting juror generation for {juror_count} jurors...'})}\n\n"
                
                # Check if NLPAgentsToolbox exists
                if not os.path.exists(nlp_toolbox_dir):
                    yield f"data: {json.dumps({'status': 'error', 'message': f'NLPAgentsToolbox not found at {nlp_toolbox_dir}'})}\n\n"
                    return
                
                # Check if system Python exists
                if not os.path.exists(system_python):
                    yield f"data: {json.dumps({'status': 'error', 'message': f'System Python not found at {system_python}'})}\n\n"
                    return
                
                # Check if scripts exist
                if not os.path.exists(mkbio_script):
                    yield f"data: {json.dumps({'status': 'error', 'message': f'mkbio.py not found at {mkbio_script}'})}\n\n"
                    return
                    
                if not os.path.exists(lsbio_script):
                    yield f"data: {json.dumps({'status': 'error', 'message': f'lsbio.py not found at {lsbio_script}'})}\n\n"
                    return
                
                # Step 1: Run mkbio.py using the virtual environment Python directly
                yield f"data: {json.dumps({'status': 'output', 'message': f'Running mkbio.py -n {juror_count}...'})}\n\n"
                
                # Prepare environment variables for the subprocess
                env = os.environ.copy()
                env['PROJECT_ROOT'] = nlp_toolbox_dir
                env['BUILD_DIR'] = os.path.join(nlp_toolbox_dir, 'build')
                env['DATABASE_FILE'] = os.path.join(nlp_toolbox_dir, 'build', 'juror.db')
                
                # Ensure build directory exists
                os.makedirs(env['BUILD_DIR'], exist_ok=True)
                
                process = subprocess.Popen(
                    [system_python, mkbio_script, '-n', str(juror_count)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    cwd=nlp_toolbox_dir,
                    env=env
                )
                
                # Stream output in real-time
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        yield f"data: {json.dumps({'status': 'output', 'message': output.strip()})}\n\n"
                
                # Get any remaining stderr
                stderr_output = process.stderr.read()
                if stderr_output:
                    yield f"data: {json.dumps({'status': 'output', 'message': f'mkbio stderr: {stderr_output.strip()}'})}\n\n"
                
                if process.returncode != 0:
                    if process.returncode is None:
                        # Check if the database file was created despite the process crashing
                        db_file = os.path.join(nlp_toolbox_dir, 'build', 'juror.db')
                        if os.path.exists(db_file):
                            yield f"data: {json.dumps({'status': 'warning', 'message': 'mkbio.py process terminated abnormally, but database file was created. Proceeding...'})}\n\n"
                        else:
                            yield f"data: {json.dumps({'status': 'error', 'message': 'mkbio.py process crashed or was terminated unexpectedly and no database was created'})}\n\n"
                            return
                    else:
                        yield f"data: {json.dumps({'status': 'error', 'message': f'mkbio.py failed with return code {process.returncode}'})}\n\n"
                        return
                
                yield f"data: {json.dumps({'status': 'output', 'message': 'mkbio.py completed successfully'})}\n\n"
                
                # Step 2: Run lsbio.py -e using the virtual environment Python directly
                yield f"data: {json.dumps({'status': 'output', 'message': 'Running lsbio.py -e...'})}\n\n"
                
                process = subprocess.Popen(
                    [system_python, lsbio_script, '-e'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    cwd=nlp_toolbox_dir,
                    env=env
                )
                
                # Stream output in real-time
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        yield f"data: {json.dumps({'status': 'output', 'message': output.strip()})}\n\n"
                
                # Get any remaining stderr
                stderr_output = process.stderr.read()
                if stderr_output:
                    yield f"data: {json.dumps({'status': 'output', 'message': f'lsbio stderr: {stderr_output.strip()}'})}\n\n"
                
                # Check the return code properly
                if process.returncode is None:
                    # Process is still running or terminated abnormally
                    # Make sure it's terminated
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except:
                        pass
                    yield f"data: {json.dumps({'status': 'warning', 'message': 'lsbio.py process may have terminated abnormally, checking output file...'})}\n\n"
                    
                    # Check if the YAML file was created successfully
                    yaml_path = os.path.join(nlp_toolbox_dir, 'build', 'jurors.yaml')
                    if os.path.exists(yaml_path):
                        yield f"data: {json.dumps({'status': 'output', 'message': f'YAML file found at {yaml_path}, continuing...'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'status': 'error', 'message': 'lsbio.py failed: no output file was created.'})}\n\n"
                        return
                elif process.returncode != 0:
                    yield f"data: {json.dumps({'status': 'warning', 'message': f'lsbio.py returned non-zero exit code {process.returncode}, checking output file...'})}\n\n"
                    
                    # Check if the YAML file was created successfully despite the error
                    yaml_path = os.path.join(nlp_toolbox_dir, 'build', 'jurors.yaml')
                    if os.path.exists(yaml_path):
                        yield f"data: {json.dumps({'status': 'output', 'message': f'YAML file found at {yaml_path}, continuing despite error...'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'status': 'error', 'message': f'lsbio.py failed with return code {process.returncode} and no output file was created.'})}\n\n"
                        return
                    
                yield f"data: {json.dumps({'status': 'output', 'message': 'lsbio.py completed successfully'})}\n\n"
                
                # Step 3: Move jurors.yaml to temp directory
                jurors_yaml_source = os.path.join(nlp_toolbox_dir, 'build', 'jurors.yaml')
                yield f"data: {json.dumps({'status': 'output', 'message': f'Looking for jurors.yaml at: {jurors_yaml_source}'})}\n\n"
                
                if os.path.exists(jurors_yaml_source):
                    filename = f"generated_jurors_{int(time.time())}.yaml"
                    jurors_yaml_dest = os.path.join(JUROR_DIR, filename)
                    shutil.copy2(jurors_yaml_source, jurors_yaml_dest)
                    
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Generated jurors saved as {filename}'})}\n\n"
                    yield f"data: {json.dumps({'status': 'completed', 'message': f'Successfully generated {juror_count} jurors', 'filename': filename})}\n\n"
                else:
                        # Check in build directory as well
                    build_dir_path = os.path.join(nlp_toolbox_dir, 'build')
                    if os.path.exists(build_dir_path):
                        build_files = os.listdir(build_dir_path)
                        yield f"data: {json.dumps({'status': 'output', 'message': f'Files in build directory: {build_files}'})}\n\n"
                    
                    # List files in the directory to help debug
                    try:
                        files_in_dir = os.listdir(nlp_toolbox_dir)
                        yield f"data: {json.dumps({'status': 'output', 'message': f'Files in NLP toolbox dir: {files_in_dir}'})}\n\n"
                    except Exception as debug_e:
                        yield f"data: {json.dumps({'status': 'output', 'message': f'Could not list directory: {str(debug_e)}'})}\n\n"
                    yield f"data: {json.dumps({'status': 'error', 'message': 'jurors.yaml not found after generation'})}\n\n"
                    
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                logger.error(f"Error in generate_jurors generator: {str(e)}")
                logger.error(f"Traceback: {error_trace}")
                yield f"data: {json.dumps({'status': 'error', 'message': f'Error during juror generation: {str(e)}'})}\n\n"
                yield f"data: {json.dumps({'status': 'error', 'message': f'Full traceback: {error_trace}'})}\n\n"
        
        return Response(generate(), 
                       mimetype='text/event-stream',
                       headers={
                           'Cache-Control': 'no-cache',
                           'Connection': 'keep-alive',
                           'Access-Control-Allow-Origin': '*',
                           'X-Accel-Buffering': 'no'
                       })
    
    except Exception as e:
        logger.error(f"Error in generate_jurors route: {str(e)}")
        def error_gen():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Route error: {str(e)}'})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream')

@app.route('/test-env')
def test_env():
    """Test endpoint to check environment variables"""
    import os
    env_info = {
        'openai_api_key_available': bool(os.environ.get('OPENAI_API_KEY')),
        'openai_api_key_length': len(os.environ.get('OPENAI_API_KEY', '')),
        'google_api_key_available': bool(os.environ.get('GOOGLE_API_KEY')),
        'google_api_key_length': len(os.environ.get('GOOGLE_API_KEY', '')),
        'port': os.environ.get('PORT', 'Not set'),
        'deployment_version': os.environ.get('DEPLOYMENT_VERSION', 'Not set'),
        'working_directory': os.getcwd(),
        'python_path': os.environ.get('PYTHONPATH', 'Not set')
    }
    return jsonify(env_info)

# Initialize API key file from environment variable if available
def initialize_api_key():
    """Create API key file from environment variable if available"""
    # Try both environment variable names for compatibility
    api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if api_key:
        # Create api_key file in backend directory where notebook expects it
        backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
        os.makedirs(backend_dir, exist_ok=True)
        api_key_path = os.path.join(backend_dir, 'api_key')
        
        try:
            with open(api_key_path, 'w') as f:
                f.write(api_key.strip())
            logger.info(f"API key file created at: {api_key_path}")
        except Exception as e:
            logger.error(f"Failed to create API key file: {e}")
    else:
        logger.warning("No OPENAI_API_KEY or GOOGLE_API_KEY environment variable found")

# Initialize API key on startup
initialize_api_key()

# Global dictionary to store active terminal sessions
active_terminals = {}

@socketio.on('start_interactive_generation')
def handle_start_interactive_generation(data):
    """Start an interactive juror generation session"""
    try:
        logger.info(f"Received start_interactive_generation event with data: {data}")
        juror_count = data.get('count', 5)
        session_id = request.sid
        logger.info(f"Starting generation for {juror_count} jurors, session_id: {session_id}")
        
        # Get the backend directory path
        backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
        nlp_toolbox_dir = os.path.join(backend_dir, 'NLPAgentsToolbox')
        
        # Use system Python instead of virtual environment Python
        system_python = '/usr/local/bin/python3'  # Docker container Python path
        mkbio_script = os.path.join(nlp_toolbox_dir, 'tools', 'mkbio.py')
        rmbio_script = os.path.join(nlp_toolbox_dir, 'tools', 'rmbio.py')
        
        logger.info(f"Backend dir: {backend_dir}")
        logger.info(f"NLP toolbox dir: {nlp_toolbox_dir}")
        logger.info(f"System python: {system_python}")
        logger.info(f"mkbio script: {mkbio_script}")
        logger.info(f"rmbio script: {rmbio_script}")
        
        emit('terminal_output', {'data': f'Starting interactive juror generation for {juror_count} jurors...\r\n'})
        
        # Check if paths exist
        if not os.path.exists(nlp_toolbox_dir):
            logger.error(f"NLPAgentsToolbox not found at {nlp_toolbox_dir}")
            emit('terminal_output', {'data': f'Error: NLPAgentsToolbox not found at {nlp_toolbox_dir}\r\n'})
            return
            
        if not os.path.exists(system_python):
            logger.error(f"System Python not found at {system_python}")
            emit('terminal_output', {'data': f'Error: System Python not found at {system_python}\r\n'})
            return
            
        if not os.path.exists(mkbio_script):
            logger.error(f"mkbio.py not found at {mkbio_script}")
            emit('terminal_output', {'data': f'Error: mkbio.py not found at {mkbio_script}\r\n'})
            return
            
        if not os.path.exists(rmbio_script):
            logger.error(f"rmbio.py not found at {rmbio_script}")
            emit('terminal_output', {'data': f'Error: rmbio.py not found at {rmbio_script}\r\n'})
            return
        
        logger.info("All paths exist, creating pseudo-terminal...")
        
        # Create a pseudo-terminal
        master_fd, slave_fd = pty.openpty()
        logger.info(f"Created pty: master_fd={master_fd}, slave_fd={slave_fd}")
        
        # Set up environment variables for the process
        env = os.environ.copy()
        
        # Read API key from the toolbox api_key file or environment variable
        api_key_file = os.path.join(nlp_toolbox_dir, 'api_key')
        if os.path.exists(api_key_file):
            with open(api_key_file, 'r') as f:
                content = f.read().strip()
                # Handle both export statement format and direct key format
                if content.startswith("export OPENAI_API_KEY="):
                    # Extract the API key from the export statement
                    api_key = content.split("'")[1] if "'" in content else content.split("=")[1].strip('"')
                else:
                    # Direct API key content
                    api_key = content
                env['OPENAI_API_KEY'] = api_key
                emit('terminal_output', {'data': 'API key loaded from file\r\n'})
                logger.info("API key loaded from file")
        elif 'OPENAI_API_KEY' in os.environ:
            # Use environment variable (for Cloud Run deployment)
            env['OPENAI_API_KEY'] = os.environ['OPENAI_API_KEY']
            emit('terminal_output', {'data': 'API key loaded from environment\r\n'})
            logger.info("API key loaded from environment variable")
        elif 'GOOGLE_API_KEY' in os.environ:
            # Use Google API key as fallback
            env['OPENAI_API_KEY'] = os.environ['GOOGLE_API_KEY']
            emit('terminal_output', {'data': 'API key loaded from Google environment\r\n'})
            logger.info("API key loaded from GOOGLE_API_KEY environment variable")
        else:
            logger.warning(f"API key not found in file {api_key_file} or environment")
            emit('terminal_output', {'data': 'Warning: No API key found\r\n'})
        
        # Run rmbio.py -A before starting the generation process
        logger.info("Running rmbio.py -A to clean up before generation...")
        emit('terminal_output', {'data': 'Cleaning up previous data with rmbio.py -A...\r\n'})
        
        try:
            rmbio_result = subprocess.run(
                [system_python, rmbio_script, '-A'],
                cwd=nlp_toolbox_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=30  # 30 second timeout
            )
            
            if rmbio_result.returncode == 0:
                logger.info("rmbio.py -A completed successfully")
                emit('terminal_output', {'data': 'Cleanup completed successfully\r\n'})
            else:
                logger.warning(f"rmbio.py -A returned code {rmbio_result.returncode}")
                emit('terminal_output', {'data': f'Cleanup completed with warnings (exit code: {rmbio_result.returncode})\r\n'})
                
            # Log any output from rmbio
            if rmbio_result.stdout:
                logger.info(f"rmbio stdout: {rmbio_result.stdout}")
            if rmbio_result.stderr:
                logger.info(f"rmbio stderr: {rmbio_result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("rmbio.py -A timed out")
            emit('terminal_output', {'data': 'Cleanup timed out, proceeding anyway...\r\n'})
        except Exception as e:
            logger.error(f"Error running rmbio.py -A: {e}")
            emit('terminal_output', {'data': f'Cleanup error: {str(e)}, proceeding anyway...\r\n'})
        
        logger.info("Starting subprocess...")
        
        # Start the process in the pseudo-terminal
        process = subprocess.Popen(
            [system_python, mkbio_script, '-n', str(juror_count)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=nlp_toolbox_dir,
            env=env,
            preexec_fn=os.setsid
        )
        
        logger.info(f"Process started with PID: {process.pid}")
        
        # Close the slave fd in the parent process
        os.close(slave_fd)
        
        # Store the terminal session
        active_terminals[session_id] = {
            'process': process,
            'master_fd': master_fd,
            'nlp_toolbox_dir': nlp_toolbox_dir,
            'juror_count': juror_count
        }
        
        logger.info(f"Stored terminal session for {session_id}")
        
        # Start a thread to read from the terminal
        def read_terminal():
            logger.info("Starting terminal reader thread...")
            try:
                # Wait for process to complete with timeout
                start_time = time.time()
                timeout = 300  # 5 minutes timeout
                
                while process.poll() is None:
                    # Check for timeout
                    if time.time() - start_time > timeout:
                        logger.error("Process timed out after 5 minutes")
                        socketio.emit('terminal_output', {'data': '\r\nProcess timed out after 5 minutes\r\n'}, room=session_id)
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except:
                            process.kill()
                        break
                    
                    # Check if there's data to read
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        try:
                            data = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                            if data:
                                logger.debug(f"Terminal output: {repr(data)}")
                                socketio.emit('terminal_output', {'data': data}, room=session_id)
                        except OSError as e:
                            logger.error(f"OSError reading from terminal: {e}")
                            break
                
                # Ensure process is finished and get final return code
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.error("Process did not terminate gracefully")
                    process.kill()
                    process.wait()
                
                return_code = process.returncode
                logger.info(f"Process finished with return code: {return_code}")
                
                # Read any remaining output from the pseudo-terminal
                try:
                    # Set non-blocking mode to read any remaining data
                    import fcntl
                    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
                    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                    
                    remaining_data = ""
                    while True:
                        try:
                            chunk = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                            if not chunk:
                                break
                            remaining_data += chunk
                        except OSError:
                            break
                    
                    if remaining_data:
                        socketio.emit('terminal_output', {'data': remaining_data}, room=session_id)
                        logger.info(f"Final output: {remaining_data}")
                        
                except Exception as e:
                    logger.error(f"Error reading final output: {e}")
                
                # Process finished, check if we need to run lsbio
                if return_code == 0:
                    socketio.emit('terminal_output', {'data': '\r\nmkbio.py completed successfully. Starting lsbio.py...\r\n'}, room=session_id)
                    run_lsbio_phase(session_id)
                elif return_code is None:
                    # Check if the database file was created despite the process crashing
                    db_file = os.path.join(nlp_toolbox_dir, 'build', 'juror.db')
                    if os.path.exists(db_file):
                        socketio.emit('terminal_output', {'data': '\r\nmkbio.py process terminated abnormally, but database file was created. Proceeding to lsbio.py...\r\n'}, room=session_id)
                        run_lsbio_phase(session_id)
                    else:
                        error_msg = '\r\nmkbio.py process crashed or was terminated unexpectedly\r\n'
                        socketio.emit('terminal_output', {'data': error_msg}, room=session_id)
                else:
                    error_msg = f'\r\nmkbio.py failed with return code {return_code}\r\n'
                    socketio.emit('terminal_output', {'data': error_msg}, room=session_id)
                    
                    # Try to get more error information by running a simpler test
                    socketio.emit('terminal_output', {'data': 'Attempting to diagnose the issue...\r\n'}, room=session_id)
                    
                    try:
                        # Test if the Python executable works
                        test_result = subprocess.run([system_python, '--version'], 
                                                   capture_output=True, text=True, timeout=10)
                        if test_result.returncode == 0:
                            socketio.emit('terminal_output', {'data': f'Python version: {test_result.stdout.strip()}\r\n'}, room=session_id)
                        else:
                            socketio.emit('terminal_output', {'data': f'Python test failed: {test_result.stderr}\r\n'}, room=session_id)
                    except Exception as e:
                        socketio.emit('terminal_output', {'data': f'Python test error: {str(e)}\r\n'}, room=session_id)
                    
                    try:
                        # Test if the script exists and is readable
                        if os.path.exists(mkbio_script):
                            socketio.emit('terminal_output', {'data': f'mkbio.py exists at {mkbio_script}\r\n'}, room=session_id)
                            # Try to read the first few lines
                            with open(mkbio_script, 'r') as f:
                                first_lines = ''.join(f.readlines()[:5])
                            socketio.emit('terminal_output', {'data': f'Script starts with:\r\n{first_lines}\r\n'}, room=session_id)
                        else:
                            socketio.emit('terminal_output', {'data': f'mkbio.py NOT FOUND at {mkbio_script}\r\n'}, room=session_id)
                    except Exception as e:
                        socketio.emit('terminal_output', {'data': f'Script check error: {str(e)}\r\n'}, room=session_id)
                    
                    cleanup_terminal(session_id)
                    
            except Exception as e:
                logger.error(f"Error reading terminal: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                socketio.emit('terminal_output', {'data': f'\r\nError reading terminal: {str(e)}\r\n'}, room=session_id)
                cleanup_terminal(session_id)
        
        thread = threading.Thread(target=read_terminal)
        thread.daemon = True
        thread.start()
        logger.info("Terminal reader thread started")
        
    except Exception as e:
        logger.error(f"Error starting interactive generation: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        emit('terminal_output', {'data': f'Error starting generation: {str(e)}\r\n'})

def run_lsbio_phase(session_id):
    """Run the lsbio.py phase after mkbio.py completes"""
    try:
        terminal_info = active_terminals.get(session_id)
        if not terminal_info:
            return
            
        nlp_toolbox_dir = terminal_info['nlp_toolbox_dir']
        system_python = '/usr/local/bin/python3'  # Docker container Python path
        lsbio_script = os.path.join(nlp_toolbox_dir, 'tools', 'lsbio.py')
        
        # Create new pseudo-terminal for lsbio
        master_fd, slave_fd = pty.openpty()
        
        # Set up environment variables for lsbio
        env = os.environ.copy()
        
        # Read API key from the toolbox api_key file
        api_key_file = os.path.join(nlp_toolbox_dir, 'api_key')
        if os.path.exists(api_key_file):
            with open(api_key_file, 'r') as f:
                content = f.read().strip()
                # Extract the API key from the export statement
                if content.startswith("export OPENAI_API_KEY="):
                    api_key = content.split("'")[1] if "'" in content else content.split("=")[1].strip('"')
                    env['OPENAI_API_KEY'] = api_key
                else:
                    env['OPENAI_API_KEY'] = content
        
        # Start lsbio process
        process = subprocess.Popen(
            [system_python, lsbio_script, '-e'],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=nlp_toolbox_dir,
            env=env,
            preexec_fn=os.setsid
        )
        
        # Close the slave fd in the parent process
        os.close(slave_fd)
        
        # Update terminal session
        terminal_info['process'] = process
        terminal_info['master_fd'] = master_fd
        
        # Start reading from lsbio terminal
        def read_lsbio_terminal():
            try:
                while process.poll() is None:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        try:
                            data = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                            if data:
                                socketio.emit('terminal_output', {'data': data}, room=session_id)
                        except OSError:
                            break
                
                # lsbio finished
                if process.returncode == 0:
                    socketio.emit('terminal_output', {'data': '\r\nlsbio.py completed successfully.\r\n'}, room=session_id)
                    # Move jurors.yaml to temp directory
                    move_generated_file(session_id)
                elif process.returncode is None:
                    # Process terminated abnormally but may have completed successfully
                    socketio.emit('terminal_output', {'data': '\r\nlsbio.py process terminated abnormally, but may have completed successfully. Checking output...\r\n'}, room=session_id)
                    
                    # Check if the YAML file was created successfully
                    yaml_path = os.path.join(nlp_toolbox_dir, 'build', 'jurors.yaml')
                    if os.path.exists(yaml_path):
                        socketio.emit('terminal_output', {'data': f'\r\nYAML file found at {yaml_path}, proceeding with execution.\r\n'}, room=session_id)
                        # Move jurors.yaml to temp directory
                        move_generated_file(session_id)
                    else:
                        socketio.emit('terminal_output', {'data': '\r\nlsbio.py failed: no output file was created.\r\n'}, room=session_id)
                else:
                    socketio.emit('terminal_output', {'data': f'\r\nlsbio.py failed with return code {process.returncode}\r\n'}, room=session_id)
                
                cleanup_terminal(session_id)
                    
            except Exception as e:
                logger.error(f"Error reading lsbio terminal: {e}")
                socketio.emit('terminal_output', {'data': f'\r\nError reading lsbio terminal: {str(e)}\r\n'}, room=session_id)
                cleanup_terminal(session_id)
        
        thread = threading.Thread(target=read_lsbio_terminal)
        thread.daemon = True
        thread.start()
        
    except Exception as e:
        logger.error(f"Error running lsbio phase: {e}")
        socketio.emit('terminal_output', {'data': f'Error running lsbio: {str(e)}\r\n'}, room=session_id)

def move_generated_file(session_id):
    """Move the generated jurors.yaml file to the temp directory"""
    try:
        terminal_info = active_terminals.get(session_id)
        if not terminal_info:
            return
            
        nlp_toolbox_dir = terminal_info['nlp_toolbox_dir']
        juror_count = terminal_info['juror_count']
        
        # Check both possible locations for jurors.yaml
        possible_locations = [
            os.path.join(nlp_toolbox_dir, 'build', 'jurors.yaml'),  # build/ subdirectory
            os.path.join(nlp_toolbox_dir, 'jurors.yaml')            # root directory
        ]
        
        jurors_yaml_source = None
        for location in possible_locations:
            if os.path.exists(location):
                jurors_yaml_source = location
                logger.info(f"Found jurors.yaml at: {location}")
                break
        
        if jurors_yaml_source:
            filename = f"generated_jurors_{int(time.time())}.yaml"
            jurors_yaml_dest = os.path.join(JUROR_DIR, filename)
            shutil.copy2(jurors_yaml_source, jurors_yaml_dest)
            
            # Store metadata for generated file (default to juror category with weight 100)
            store_file_metadata(filename, 'juror', 100, generated=True)
            
            socketio.emit('terminal_output', {'data': f'Generated jurors saved as {filename}\r\n'}, room=session_id)
            socketio.emit('generation_completed', {'filename': filename, 'count': juror_count}, room=session_id)
        else:
            # Debug: List files in both locations
            logger.warning("jurors.yaml not found, checking directories...")
            for check_dir in [nlp_toolbox_dir, os.path.join(nlp_toolbox_dir, 'build')]:
                if os.path.exists(check_dir):
                    try:
                        files = os.listdir(check_dir)
                        logger.info(f"Files in {check_dir}: {files}")
                        socketio.emit('terminal_output', {'data': f'Files in {check_dir}: {files}\r\n'}, room=session_id)
                    except Exception as e:
                        logger.error(f"Error listing {check_dir}: {e}")
            
            socketio.emit('terminal_output', {'data': 'Warning: jurors.yaml not found after generation\r\n'}, room=session_id)
            
    except Exception as e:
        logger.error(f"Error moving generated file: {e}")
        socketio.emit('terminal_output', {'data': f'Error saving generated file: {str(e)}\r\n'}, room=session_id)

@socketio.on('terminal_input')
def handle_terminal_input(data):
    """Handle input from the user to the terminal"""
    try:
        session_id = request.sid
        terminal_info = active_terminals.get(session_id)
        
        if terminal_info and 'master_fd' in terminal_info:
            user_input = data.get('input', '')
            os.write(terminal_info['master_fd'], user_input.encode('utf-8'))
            
    except Exception as e:
        logger.error(f"Error handling terminal input: {e}")
        emit('terminal_output', {'data': f'Error sending input: {str(e)}\r\n'})

@socketio.on('disconnect')
def handle_disconnect():
    """Clean up when client disconnects"""
    session_id = request.sid
    cleanup_terminal(session_id)

def cleanup_terminal(session_id):
    """Clean up terminal session"""
    try:
        terminal_info = active_terminals.pop(session_id, None)
        if terminal_info:
            try:
                if 'process' in terminal_info and terminal_info['process']:
                    terminal_info['process'].terminate()
            except:
                pass
            try:
                if 'master_fd' in terminal_info:
                    os.close(terminal_info['master_fd'])
            except:
                pass
    except Exception as e:
        logger.error(f"Error cleaning up terminal: {e}")

# Global storage for file metadata (in production, use a database or session storage)
FILE_METADATA = {}

def store_file_metadata(filename, category, weight, generated=False):
    """Store file metadata for later retrieval"""
    FILE_METADATA[filename] = {
        'category': category,
        'weight': weight,
        'generated': generated
    }

def get_file_metadata(filename):
    """Get file metadata"""
    return FILE_METADATA.get(filename, {'category': 'juror', 'weight': 100, 'generated': False})

def get_all_file_metadata():
    """Get all file metadata"""
    return FILE_METADATA.copy()

def clear_file_metadata():
    """Clear all file metadata"""
    FILE_METADATA.clear()

# Add startup logging for debugging
logger.info(f"Flask app starting - Environment: {os.environ.get('FLASK_ENV', 'production')}")
logger.info(f"Port: {PORT}, Host: {HOST}")
logger.info(f"Debug mode: {DEBUG}")

# WSGI entry point for production servers like Gunicorn
def create_app():
    """Application factory for WSGI servers"""
    logger.info("App factory called - returning Flask app instance with SocketIO")
    return app

# Make the Flask app available for WSGI servers
# The SocketIO instance will handle WebSocket connections automatically
application = app

if __name__ == "__main__":
    try:
        # Use production-safe server settings
        if DEBUG:
            socketio.run(app, debug=True, port=PORT, host=HOST)
        else:
            # In production, use a proper WSGI server
            socketio.run(app, debug=False, port=PORT, host=HOST)
    finally:
        # Cleanup temp directory on exit
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            logger.info("Cleaned up temporary directory")

@app.route('/debug-files')
def debug_files():
    """Debug endpoint to check what files exist in the container"""
    import os
    debug_info = {
        'working_directory': os.getcwd(),
        'app_directory_contents': [],
        'backend_exists': False,
        'nlp_toolbox_exists': False,
        'tools_exist': False,
        'python_paths': []
    }
    
    try:
        debug_info['app_directory_contents'] = os.listdir('/app')
    except Exception as e:
        debug_info['app_directory_error'] = str(e)
    
    backend_dir = '/app/backend'
    if os.path.exists(backend_dir):
        debug_info['backend_exists'] = True
        try:
            debug_info['backend_contents'] = os.listdir(backend_dir)
        except Exception as e:
            debug_info['backend_error'] = str(e)
            
        nlp_toolbox_dir = os.path.join(backend_dir, 'NLPAgentsToolbox')
        if os.path.exists(nlp_toolbox_dir):
            debug_info['nlp_toolbox_exists'] = True
            try:
                debug_info['nlp_toolbox_contents'] = os.listdir(nlp_toolbox_dir)
            except Exception as e:
                debug_info['nlp_toolbox_error'] = str(e)
                
            tools_dir = os.path.join(nlp_toolbox_dir, 'tools')
            if os.path.exists(tools_dir):
                debug_info['tools_exist'] = True
                try:
                    debug_info['tools_contents'] = os.listdir(tools_dir)
                except Exception as e:
                    debug_info['tools_error'] = str(e)
    
    # Check for Python executables
    possible_python_paths = [
        '/usr/local/bin/python3',
        '/usr/bin/python3',
        '/bin/python3',
        'python3',
        'python'
    ]
    
    for path in possible_python_paths:
        if os.path.exists(path):
            debug_info['python_paths'].append(path)
    
    return jsonify(debug_info)

@app.route('/debug-nlp-toolbox')
def debug_nlp_toolbox():
    """Debug endpoint to test NLPAgentsToolbox components"""
    debug_info = {
        'timestamp': time.time(),
        'working_directory': os.getcwd(),
        'python_executable_test': {},
        'mkbio_script_test': {},
        'environment_variables': {},
        'directory_structure': {}
    }
    
    # Test Python executable
    system_python = '/usr/local/bin/python3'
    try:
        result = subprocess.run([system_python, '--version'], 
                              capture_output=True, text=True, timeout=10)
        debug_info['python_executable_test'] = {
            'executable': system_python,
            'exists': os.path.exists(system_python),
            'version_output': result.stdout.strip() if result.returncode == 0 else None,
            'version_error': result.stderr.strip() if result.stderr else None,
            'return_code': result.returncode
        }
    except Exception as e:
        debug_info['python_executable_test']['error'] = str(e)
    
    # Test mkbio script
    backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
    nlp_toolbox_dir = os.path.join(backend_dir, 'NLPAgentsToolbox')
    mkbio_script = os.path.join(nlp_toolbox_dir, 'tools', 'mkbio.py')
    
    debug_info['mkbio_script_test'] = {
        'script_path': mkbio_script,
        'script_exists': os.path.exists(mkbio_script),
        'nlp_toolbox_exists': os.path.exists(nlp_toolbox_dir),
        'backend_exists': os.path.exists(backend_dir)
    }
    
    if os.path.exists(mkbio_script):
        try:
            with open(mkbio_script, 'r') as f:
                first_10_lines = ''.join(f.readlines()[:10])
            debug_info['mkbio_script_test']['first_lines'] = first_10_lines
        except Exception as e:
            debug_info['mkbio_script_test']['read_error'] = str(e)
    
    # Test environment variables
    debug_info['environment_variables'] = {
        'OPENAI_API_KEY': 'SET' if os.environ.get('OPENAI_API_KEY') else 'NOT SET',
        'OPENAI_API_KEY_LENGTH': len(os.environ.get('OPENAI_API_KEY', '')),
        'PATH': os.environ.get('PATH', 'Not set'),
        'PYTHONPATH': os.environ.get('PYTHONPATH', 'Not set'),
        'BUILD_DIR': os.environ.get('BUILD_DIR', 'Not set'),
        'API_CENSUS': os.environ.get('API_CENSUS', 'Not set')
    }
    
    # Test directory structure
    if os.path.exists(nlp_toolbox_dir):
        try:
            debug_info['directory_structure']['nlp_toolbox_contents'] = os.listdir(nlp_toolbox_dir)
            
            tools_dir = os.path.join(nlp_toolbox_dir, 'tools')
            if os.path.exists(tools_dir):
                debug_info['directory_structure']['tools_contents'] = os.listdir(tools_dir)
                
            stages_dir = os.path.join(nlp_toolbox_dir, 'stages')
            if os.path.exists(stages_dir):
                debug_info['directory_structure']['stages_contents'] = os.listdir(stages_dir)
                
        except Exception as e:
            debug_info['directory_structure']['error'] = str(e)
    
    # Test simple Python execution in the nlp_toolbox directory
    try:
        simple_test = subprocess.run(
            [system_python, '-c', 'import sys; print("Python works:", sys.version)'],
            cwd=nlp_toolbox_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        debug_info['simple_python_test'] = {
            'return_code': simple_test.returncode,
            'stdout': simple_test.stdout,
            'stderr': simple_test.stderr
        }
    except Exception as e:
        debug_info['simple_python_test'] = {'error': str(e)}
    
    # Test mkbio.py help command
    try:
        mkbio_help = subprocess.run(
            [system_python, mkbio_script, '--help'],
            cwd=nlp_toolbox_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        debug_info['mkbio_help_test'] = {
            'return_code': mkbio_help.returncode,
            'stdout': mkbio_help.stdout[:500] + ('...' if len(mkbio_help.stdout) > 500 else ''),
            'stderr': mkbio_help.stderr[:500] + ('...' if len(mkbio_help.stderr) > 500 else '')
        }
    except Exception as e:
        debug_info['mkbio_help_test'] = {'error': str(e)}
    
    return jsonify(debug_info)

@app.route('/debug-temp-dirs')
def debug_temp_dirs():
    """Debug endpoint to inspect temporary directories and their contents"""
    debug_info = {
        'timestamp': time.time(),
        'temp_root': '/tmp',
        'temp_directories': [],
        'jury_download_dirs': [],
        'working_directory': os.getcwd()
    }
    
    try:
        # List all /tmp contents
        if os.path.exists('/tmp'):
            temp_contents = os.listdir('/tmp')
            debug_info['temp_contents'] = temp_contents
            
            # Find jury download directories
            jury_dirs = [d for d in temp_contents if d.startswith('jury_downloads_')]
            debug_info['jury_download_dirs'] = jury_dirs
            
            # Inspect each jury download directory
            for jury_dir in jury_dirs:
                jury_path = os.path.join('/tmp', jury_dir)
                try:
                    jury_info = {
                        'name': jury_dir,
                        'path': jury_path,
                        'exists': os.path.exists(jury_path),
                        'is_directory': os.path.isdir(jury_path),
                        'contents': []
                    }
                    
                    if os.path.isdir(jury_path):
                        jury_contents = os.listdir(jury_path)
                        jury_info['contents'] = jury_contents
                        
                        # Get details for each file/folder in jury directory
                        detailed_contents = []
                        for item in jury_contents:
                            item_path = os.path.join(jury_path, item)
                            try:
                                item_info = {
                                    'name': item,
                                    'path': item_path,
                                    'is_file': os.path.isfile(item_path),
                                    'is_directory': os.path.isdir(item_path),
                                    'size': os.path.getsize(item_path) if os.path.isfile(item_path) else None
                                }
                                
                                # If it's a file, try to read first few lines
                                if os.path.isfile(item_path) and item_info['size'] and item_info['size'] < 10000:
                                    try:
                                        with open(item_path, 'r', encoding='utf-8', errors='ignore') as f:
                                            item_info['preview'] = f.read(500)  # First 500 chars
                                    except Exception as e:
                                        item_info['preview_error'] = str(e)
                                
                                # If it's a directory, list its contents
                                if os.path.isdir(item_path):
                                    try:
                                        item_info['subdirectory_contents'] = os.listdir(item_path)
                                    except Exception as e:
                                        item_info['subdirectory_error'] = str(e)
                                
                                detailed_contents.append(item_info)
                            except Exception as e:
                                detailed_contents.append({
                                    'name': item,
                                    'error': str(e)
                                })
                        
                        jury_info['detailed_contents'] = detailed_contents
                    
                    debug_info['temp_directories'].append(jury_info)
                    
                except Exception as e:
                    debug_info['temp_directories'].append({
                        'name': jury_dir,
                        'error': str(e)
                    })
        else:
            debug_info['temp_error'] = '/tmp directory does not exist'
            
    except Exception as e:
        debug_info['error'] = str(e)
    
    return jsonify(debug_info)

@app.route('/debug-filesystem')
def debug_filesystem():
    """Debug endpoint to browse the filesystem structure (excludes sensitive files)"""
    path = request.args.get('path', '/tmp')
    
    # Security: Block access to sensitive paths
    sensitive_patterns = [
        'api_key', 'secret', 'password', 'token', 'credential',
        '.env', 'config', 'private', '.ssh', '.pem', '.key'
    ]
    
    debug_info = {
        'timestamp': time.time(),
        'requested_path': path,
        'exists': False,
        'is_directory': False,
        'contents': [],
        'parent_directory': None,
        'breadcrumb': [],
        'security_note': 'Sensitive files are filtered for security'
    }
    
    try:
        # Normalize and validate path
        normalized_path = os.path.normpath(path)
        
        # Security check: Don't allow access to sensitive file patterns
        if any(pattern in normalized_path.lower() for pattern in sensitive_patterns):
            debug_info['error'] = 'Access to sensitive files is restricted'
            return jsonify(debug_info)
        debug_info['normalized_path'] = normalized_path
        
        # Check if path exists
        if os.path.exists(normalized_path):
            debug_info['exists'] = True
            debug_info['is_directory'] = os.path.isdir(normalized_path)
            
            # Generate breadcrumb
            path_parts = normalized_path.split('/')
            breadcrumb = []
            current = ''
            for part in path_parts:
                if part:  # Skip empty parts
                    current = os.path.join(current, part) if current else '/' + part
                    breadcrumb.append({'name': part, 'path': current})
                elif not current:  # Root
                    current = '/'
                    breadcrumb.append({'name': 'root', 'path': '/'})
            debug_info['breadcrumb'] = breadcrumb
            
            # Get parent directory
            parent = os.path.dirname(normalized_path)
            if parent != normalized_path:  # Avoid infinite loop at root
                debug_info['parent_directory'] = parent
            
            if debug_info['is_directory']:
                try:
                    items = os.listdir(normalized_path)
                    contents = []
                    
                    # Filter out sensitive files
                    filtered_items = []
                    for item in items:
                        if not any(pattern in item.lower() for pattern in sensitive_patterns):
                            filtered_items.append(item)
                        else:
                            # Log that we filtered out a sensitive file (for debugging)
                            logger.info(f"Filtered sensitive file from debug browser: {item}")
                    
                    for item in sorted(filtered_items):
                        item_path = os.path.join(normalized_path, item)
                        try:
                            stat_info = os.stat(item_path)
                            item_info = {
                                'name': item,
                                'path': item_path,
                                'is_file': os.path.isfile(item_path),
                                'is_directory': os.path.isdir(item_path),
                                'size': stat_info.st_size if os.path.isfile(item_path) else None,
                                'modified': stat_info.st_mtime,
                                'permissions': oct(stat_info.st_mode)[-3:]
                            }
                            
                            # For small text files, provide a preview
                            if (os.path.isfile(item_path) and 
                                item_info['size'] and 
                                item_info['size'] < 5000 and
                                item.lower().endswith(('.txt', '.yaml', '.yml', '.json', '.py', '.md', '.log'))):
                                try:
                                    with open(item_path, 'r', encoding='utf-8', errors='ignore') as f:
                                        item_info['preview'] = f.read(1000)  # First 1000 chars
                                except Exception as e:
                                    item_info['preview_error'] = str(e)
                            
                            contents.append(item_info)
                            
                        except Exception as e:
                            contents.append({
                                'name': item,
                                'path': item_path,
                                'error': str(e)
                            })
                    
                    debug_info['contents'] = contents
                    debug_info['total_items'] = len(contents)
                    
                except Exception as e:
                    debug_info['directory_error'] = str(e)
            else:
                # It's a file, try to read it
                try:
                    stat_info = os.stat(normalized_path)
                    debug_info['file_info'] = {
                        'size': stat_info.st_size,
                        'modified': stat_info.st_mtime,
                        'permissions': oct(stat_info.st_mode)[-3:]
                    }
                    
                    # Try to read file content if it's small and text-like
                    if (stat_info.st_size < 50000 and 
                        any(normalized_path.lower().endswith(ext) for ext in 
                            ['.txt', '.yaml', '.yml', '.json', '.py', '.md', '.log', '.csv', '.conf'])):
                        try:
                            with open(normalized_path, 'r', encoding='utf-8', errors='ignore') as f:
                                debug_info['file_content'] = f.read()
                        except Exception as e:
                            debug_info['file_read_error'] = str(e)
                    else:
                        debug_info['file_too_large_or_binary'] = True
                        
                except Exception as e:
                    debug_info['file_stat_error'] = str(e)
        else:
            debug_info['error'] = 'Path does not exist'
            
    except Exception as e:
        debug_info['error'] = str(e)
    
    return jsonify(debug_info)

@app.route('/debug-filesystem-browser')
def debug_filesystem_browser():
    """Web interface for browsing the filesystem"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Filesystem Browser</title>
        <style>
            body { font-family: monospace; background: #1a1d23; color: #c2c7d0; padding: 20px; }
            .breadcrumb { margin-bottom: 20px; }
            .breadcrumb a { color: #01aaff; text-decoration: none; margin-right: 5px; }
            .breadcrumb a:hover { text-decoration: underline; }
            .item { margin: 5px 0; padding: 5px; border-radius: 3px; }
            .item:hover { background: #2a2f3a; }
            .directory { color: #79c0ff; font-weight: bold; }
            .file { color: #c2c7d0; }
            .size { color: #7b8495; font-size: 0.9em; margin-left: 10px; }
            .preview { background: #11141a; padding: 10px; margin: 10px 0; border-radius: 5px; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }
            .error { color: #dc3545; }
            .parent { color: #969eaf; }
            input { background: #11141a; color: #c2c7d0; border: 1px solid #202632; padding: 5px; width: 400px; }
            button { background: #0172ad; color: white; border: none; padding: 5px 10px; margin-left: 5px; }
        </style>
    </head>
    <body>
        <h1>Filesystem Browser</h1>
        <div>
            <input type="text" id="pathInput" placeholder="/tmp" value="/tmp">
            <button onclick="browsePath()">Browse</button>
            <button onclick="browsePath('/tmp')">Go to /tmp</button>
            <button onclick="browsePath('/app')">Go to /app</button>
            <button onclick="browsePath('/')">Go to Root</button>
        </div>
        
        <div id="content"></div>
        
        <script>
            function browsePath(path) {
                if (path) {
                    document.getElementById('pathInput').value = path;
                }
                
                const inputPath = document.getElementById('pathInput').value || '/tmp';
                fetch('/debug-filesystem?path=' + encodeURIComponent(inputPath))
                    .then(response => response.json())
                    .then(data => {
                        displayContent(data);
                    })
                    .catch(error => {
                        document.getElementById('content').innerHTML = '<div class="error">Error: ' + error + '</div>';
                    });
            }
            
            function displayContent(data) {
                let html = '';
                
                if (data.error) {
                    html += '<div class="error">Error: ' + data.error + '</div>';
                    document.getElementById('content').innerHTML = html;
                    return;
                }
                
                // Breadcrumb
                if (data.breadcrumb && data.breadcrumb.length > 0) {
                    html += '<div class="breadcrumb">';
                    data.breadcrumb.forEach(item => {
                        html += '<a href="#" onclick="browsePath(\\'' + item.path + '\\')">' + item.name + '</a> / ';
                    });
                    html += '</div>';
                }
                
                // Parent directory link
                if (data.parent_directory) {
                    html += '<div class="item parent"><a href="#" onclick="browsePath(\\'' + data.parent_directory + '\\')">.. (parent directory)</a></div>';
                }
                
                // Current path info
                html += '<div style="margin: 10px 0; color: #7b8495;">Path: ' + data.normalized_path + '</div>';
                
                if (data.is_directory && data.contents) {
                    html += '<div style="margin: 10px 0; color: #7b8495;">Items: ' + data.total_items + '</div>';
                    
                    data.contents.forEach(item => {
                        const className = item.is_directory ? 'directory' : 'file';
                        const icon = item.is_directory ? '📁' : '📄';
                        const size = item.size ? ' (' + formatBytes(item.size) + ')' : '';
                        
                        html += '<div class="item ' + className + '">';
                        if (item.is_directory) {
                            html += '<a href="#" onclick="browsePath(\\'' + item.path + '\\')">' + icon + ' ' + item.name + '</a>';
                        } else {
                            html += '<span onclick="browsePath(\\'' + item.path + '\\')" style="cursor: pointer;">' + icon + ' ' + item.name + '</span>';
                        }
                        html += '<span class="size">' + size + '</span>';
                        html += '</div>';
                        
                        if (item.preview) {
                            html += '<div class="preview">' + escapeHtml(item.preview) + '</div>';
                        }
                    });
                } else if (!data.is_directory && data.file_content) {
                    html += '<div class="preview">' + escapeHtml(data.file_content) + '</div>';
                }
                
                document.getElementById('content').innerHTML = html;
            }
            
            function formatBytes(bytes) {
                if (bytes === 0) return '0 B';
                const k = 1024;
                const sizes = ['B', 'KB', 'MB', 'GB'];
                const i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
            }
            
            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }
            
            // Load /tmp by default
            browsePath('/tmp');
        </script>
    </body>
    </html>
    """