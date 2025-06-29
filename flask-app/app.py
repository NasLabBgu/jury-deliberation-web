from flask import Flask, render_template, request, jsonify
import os
import tempfile
import shutil
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
        files_data = request.get_json()
        
        if not files_data or 'files' not in files_data:
            return jsonify({'error': 'No files data provided'}), 400
        
        # Clear existing files in both directories before uploading new ones
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
        
        for file_info in files_data['files']:
            filename = secure_filename(file_info['filename'])
            category = file_info.get('category', 'juror')  # default to juror
            weight = file_info.get('weight', 100)  # default to 100
            
            # Determine target directory
            target_dir = JUROR_DIR if category == 'juror' else CASE_DIR
            
            # For now, create placeholder files (since we're getting filenames, not actual file data)
            # In a real implementation, you'd handle the actual file upload
            filepath = os.path.join(target_dir, filename)
            
            # Create placeholder file with weight information
            with open(filepath, 'w') as f:
                f.write(f"Placeholder for {filename} (category: {category}, weight: {weight})")
            
            print(f"Created: {filepath} (weight: {weight})")
            
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
        
        result = {
            'success': True,
            'message': f'Process started with {juror_count} jurors, {repeat_count} repetitions',
            'juror_files': juror_files,
            'case_files': case_files,
            'evaluation_options': evaluation_options,
            'temp_dir': TEMP_DIR
        }
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    try:
        app.run(debug=True, port=5001, host="127.0.0.1")
    finally:
        # Cleanup temp directory on exit
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)