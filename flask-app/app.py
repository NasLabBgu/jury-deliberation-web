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

# Setup paths and environment
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
                
                if process.returncode != 0:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'lsbio.py failed with return code {process.returncode}'})}\n\n"
                    return
                    
                yield f"data: {json.dumps({'status': 'output', 'message': 'lsbio.py completed successfully'})}\n\n"
                
                # Step 3: Move jurors.yaml to temp directory
                jurors_yaml_source = os.path.join(nlp_toolbox_dir, 'jurors.yaml')
                yield f"data: {json.dumps({'status': 'output', 'message': f'Looking for jurors.yaml at: {jurors_yaml_source}'})}\n\n"
                
                if os.path.exists(jurors_yaml_source):
                    filename = f"generated_jurors_{int(time.time())}.yaml"
                    jurors_yaml_dest = os.path.join(JUROR_DIR, filename)
                    shutil.copy2(jurors_yaml_source, jurors_yaml_dest)
                    
                    yield f"data: {json.dumps({'status': 'output', 'message': f'Generated jurors saved as {filename}'})}\n\n"
                    yield f"data: {json.dumps({'status': 'completed', 'message': f'Successfully generated {juror_count} jurors', 'filename': filename})}\n\n"
                else:
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
                else:
                    error_msg = f'\r\nmkbio.py failed with return code {return_code}\r\n'
                    if return_code is None:
                        error_msg = '\r\nmkbio.py process crashed or was terminated unexpectedly\r\n'
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