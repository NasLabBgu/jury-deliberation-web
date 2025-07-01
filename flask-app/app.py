import os
import tempfile
import shutil
import json
import subprocess
import threading
import queue
import time
import logging
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, Response
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
        # Check if the request contains files
        if 'files' not in request.files:
            logger.warning("Upload attempt with no files")
            return jsonify({'error': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            logger.warning("Upload attempt with empty files")
            return jsonify({'error': 'No files selected'}), 400
        
        # Validate file types for security
        allowed_extensions = {'.yaml', '.yml', '.txt'}
        for file in files:
            if file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext not in allowed_extensions:
                    logger.warning(f"Rejected file with invalid extension: {file.filename}")
                    return jsonify({'error': f'Invalid file type: {ext}. Only .yaml, .yml, and .txt files are allowed.'}), 400
        
        # Get additional data about categories and weights
        categories = request.form.getlist('categories')
        weights = request.form.getlist('weights')
        
        # Clear existing files first
        logger.info("Clearing existing files...")
        
        # Clear juror directory
        if os.path.exists(JUROR_DIR):
            for filename in os.listdir(JUROR_DIR):
                filepath = os.path.join(JUROR_DIR, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)
                    logger.info(f"Deleted: {filepath}")
        
        # Clear case directory
        if os.path.exists(CASE_DIR):
            for filename in os.listdir(CASE_DIR):
                filepath = os.path.join(CASE_DIR, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)
                    logger.info(f"Deleted: {filepath}")
        
        logger.info("All existing files cleared.")
        
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
        
        return jsonify({
            'success': True,
            'uploaded_files': results,
            'temp_dir': TEMP_DIR,
            'message': f'Cleared existing files and uploaded {len(results)} new files'
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
            'Access-Control-Allow-Origin': '*'
        })
        
    except Exception as e:
        def error_gen():
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        return Response(error_gen(), mimetype='text/event-stream')

@app.route('/test-env')
def test_env():
    """Test endpoint to check environment variables"""
    import os
    env_info = {
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
    api_key = os.environ.get('GOOGLE_API_KEY')
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
        logger.warning("No GOOGLE_API_KEY environment variable found")

# Initialize API key on startup
initialize_api_key()

# Add startup logging for debugging
logger.info(f"Flask app starting - Environment: {os.environ.get('FLASK_ENV', 'production')}")
logger.info(f"Port: {PORT}, Host: {HOST}")
logger.info(f"Debug mode: {DEBUG}")

# WSGI entry point for production servers like Gunicorn
def create_app():
    """Application factory for WSGI servers"""
    logger.info("App factory called - returning Flask app instance")
    return app

# Make the app available for WSGI servers
application = app

if __name__ == "__main__":
    try:
        # Use production-safe server settings
        if DEBUG:
            app.run(debug=True, port=PORT, host=HOST)
        else:
            # In production, use a proper WSGI server
            from waitress import serve
            logger.info(f"Starting production server on {HOST}:{PORT}")
            serve(app, host=HOST, port=PORT)
    except ImportError:
        # Fallback if waitress is not available
        logger.warning("Waitress not available, using Flask dev server")
        app.run(debug=False, port=PORT, host=HOST)
    finally:
        # Cleanup temp directory on exit
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            logger.info("Cleaned up temporary directory")