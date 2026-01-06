#!/usr/bin/env python3
"""
EPUB & Web Article to Markdown Converter - Web GUI
A Flask-based web interface for converting EPUBs and web articles to AI-optimized Markdown.
"""

import os
import sys
import json
import tempfile
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from epub_to_md_converter import process_folder, check_pandoc_installed
import threading
import queue
import io

# Try to import HTML converter (may not be available if dependencies missing)
HTML_CONVERTER_AVAILABLE = False
HTML_DEPENDENCIES_MISSING = []
try:
    from html_to_md_converter import convert_url_to_markdown, check_dependencies
    deps_ok, missing = check_dependencies()
    if deps_ok:
        HTML_CONVERTER_AVAILABLE = True
    else:
        HTML_DEPENDENCIES_MISSING = missing
except ImportError as e:
    HTML_DEPENDENCIES_MISSING = ['html_to_md_converter module']

# Try to import PDF converter (may not be available if dependencies missing)
PDF_CONVERTER_AVAILABLE = False
PDF_DEPENDENCIES_MISSING = []
try:
    from pdf_to_md_converter import convert_pdf_to_markdown, check_dependencies as check_pdf_dependencies
    pdf_deps_ok, pdf_missing = check_pdf_dependencies()
    if pdf_deps_ok:
        PDF_CONVERTER_AVAILABLE = True
    else:
        PDF_DEPENDENCIES_MISSING = pdf_missing
except ImportError as e:
    PDF_DEPENDENCIES_MISSING = ['pdf_to_md_converter module']

# Preferences file location (in user's home directory)
PREFERENCES_FILE = os.path.join(os.path.expanduser('~'), '.epub2md_preferences.json')


def get_downloads_folder():
    """Get the user's Downloads folder path (cross-platform)"""
    home = Path.home()
    downloads = home / "Downloads"
    if downloads.exists():
        return str(downloads)
    return str(home)


def load_preferences():
    """Load user preferences from file"""
    try:
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading preferences: {e}")
    return {}


def save_preferences(prefs):
    """Save user preferences to file"""
    try:
        with open(PREFERENCES_FILE, 'w') as f:
            json.dump(prefs, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving preferences: {e}")
        return False

app = Flask(__name__)

# Global variables for EPUB conversion status
conversion_status = {
    'running': False,
    'progress': [],
    'current': 0,
    'total': 0,
    'completed': False
}

# Global variables for URL conversion status
url_conversion_status = {
    'running': False,
    'progress': [],
    'completed': False,
    'success': False,
    'output_file': None,
    'error': None
}

# Global variables for PDF conversion status
pdf_conversion_status = {
    'running': False,
    'progress': [],
    'completed': False,
    'success': False,
    'output_file': None,
    'error': None
}

class OutputCapture:
    """Capture stdout for progress reporting"""
    def __init__(self):
        self.queue = queue.Queue()

    def write(self, text):
        if text.strip():
            self.queue.put(text)
            conversion_status['progress'].append(text)
        sys.__stdout__.write(text)

    def flush(self):
        sys.__stdout__.flush()


@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html')


@app.route('/check_pandoc')
def check_pandoc():
    """Check if Pandoc is installed"""
    installed = check_pandoc_installed()
    return jsonify({'installed': installed})


@app.route('/convert', methods=['POST'])
def convert():
    """Start the conversion process"""
    global conversion_status

    data = request.json
    input_folder = data.get('input_folder', '')
    output_folder = data.get('output_folder', 'md processed books')

    if not input_folder:
        return jsonify({'error': 'Input folder is required'}), 400

    # Validate input folder
    if not os.path.exists(input_folder):
        return jsonify({'error': f'Input folder does not exist: {input_folder}'}), 400

    if not os.path.isdir(input_folder):
        return jsonify({'error': f'Input path is not a folder: {input_folder}'}), 400

    # Reset status
    conversion_status = {
        'running': True,
        'progress': [],
        'current': 0,
        'total': 0,
        'completed': False
    }

    # Run conversion in background thread
    def run_conversion():
        global conversion_status
        try:
            # Capture output
            old_stdout = sys.stdout
            sys.stdout = OutputCapture()

            # Run conversion
            process_folder(input_folder, output_folder)

            # Restore stdout
            sys.stdout = old_stdout

            conversion_status['completed'] = True
            conversion_status['running'] = False

        except Exception as e:
            conversion_status['progress'].append(f"Error: {str(e)}")
            conversion_status['running'] = False
            conversion_status['completed'] = True

    thread = threading.Thread(target=run_conversion)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started'})


@app.route('/status')
def status():
    """Get conversion status"""
    return jsonify(conversion_status)


@app.route('/browse_folder', methods=['POST'])
def browse_folder():
    """Get folder contents for browsing"""
    data = request.json
    path = data.get('path', os.path.expanduser('~'))

    try:
        # Expand user home directory
        path = os.path.expanduser(path)

        # Get absolute path
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        # Check if path exists
        if not os.path.exists(path):
            return jsonify({'error': 'Path does not exist'}), 400

        # If it's a file, get its directory
        if os.path.isfile(path):
            path = os.path.dirname(path)

        # Get parent directory
        parent = os.path.dirname(path)

        # Get directory contents
        items = []
        try:
            for item in sorted(os.listdir(path)):
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    items.append({
                        'name': item,
                        'path': item_path,
                        'type': 'folder'
                    })
        except PermissionError:
            pass

        return jsonify({
            'current': path,
            'parent': parent if parent != path else None,
            'items': items
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/get_preferences')
def get_preferences():
    """Get saved preferences or defaults"""
    prefs = load_preferences()
    downloads = get_downloads_folder()

    return jsonify({
        'input_folder': prefs.get('input_folder', downloads),
        'output_folder': prefs.get('output_folder', downloads),
        'has_saved_prefs': bool(prefs)
    })


@app.route('/save_preferences', methods=['POST'])
def save_prefs():
    """Save user preferences"""
    data = request.json
    prefs = load_preferences()

    if 'input_folder' in data:
        prefs['input_folder'] = data['input_folder']
    if 'output_folder' in data:
        prefs['output_folder'] = data['output_folder']

    success = save_preferences(prefs)
    return jsonify({'success': success})


@app.route('/upload_file', methods=['POST'])
def upload_file():
    """Handle file upload from drag and drop"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided', 'success': False}), 400

    file = request.files['file']
    target_folder = request.form.get('target_folder', '')

    if not file.filename:
        return jsonify({'error': 'No file selected', 'success': False}), 400

    if not target_folder:
        return jsonify({'error': 'No target folder specified', 'success': False}), 400

    # Validate file extension
    if not file.filename.lower().endswith('.epub'):
        return jsonify({'error': 'Only EPUB files are allowed', 'success': False}), 400

    try:
        # Expand and validate target folder
        target_folder = os.path.expanduser(target_folder)

        # Create folder if it doesn't exist
        if not os.path.exists(target_folder):
            os.makedirs(target_folder)

        # Save file
        file_path = os.path.join(target_folder, file.filename)
        file.save(file_path)

        return jsonify({
            'success': True,
            'path': file_path,
            'filename': file.filename
        })

    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500


def open_folder_dialog_native(initial_dir, title):
    """
    Try to open a native folder dialog using various methods.
    Returns (path, success, error_message)
    """
    import subprocess
    import shutil

    # Ensure initial_dir exists
    initial_dir = os.path.expanduser(initial_dir)
    if not os.path.exists(initial_dir):
        initial_dir = get_downloads_folder()

    # Method 1: Try tkinter
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        folder_path = filedialog.askdirectory(
            initialdir=initial_dir,
            title=title
        )
        root.destroy()

        if folder_path:
            return folder_path, True, None
        else:
            return '', False, None  # User cancelled
    except ImportError:
        pass  # tkinter not available, try next method
    except Exception as e:
        pass  # tkinter failed, try next method

    # Method 2: Try zenity (Linux/GNOME)
    if shutil.which('zenity'):
        try:
            result = subprocess.run(
                ['zenity', '--file-selection', '--directory',
                 '--title=' + title, '--filename=' + initial_dir + '/'],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), True, None
            elif result.returncode == 1:
                return '', False, None  # User cancelled
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    # Method 3: Try kdialog (Linux/KDE)
    if shutil.which('kdialog'):
        try:
            result = subprocess.run(
                ['kdialog', '--getexistingdirectory', initial_dir, '--title', title],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), True, None
            elif result.returncode == 1:
                return '', False, None  # User cancelled
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    # Method 4: Try osascript (macOS)
    if shutil.which('osascript'):
        try:
            script = f'''
            set folderPath to POSIX path of (choose folder with prompt "{title}" default location POSIX file "{initial_dir}")
            return folderPath
            '''
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().rstrip('/'), True, None
            elif result.returncode == 1:
                return '', False, None  # User cancelled
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    # No native dialog method available
    return '', False, 'No native dialog tool available (install zenity, kdialog, or tkinter)'


@app.route('/native_folder_dialog', methods=['POST'])
def native_folder_dialog():
    """Open native folder picker dialog using best available method"""
    data = request.json
    initial_dir = data.get('initial_dir', get_downloads_folder())
    title = data.get('title', 'Select Folder')

    path, success, error = open_folder_dialog_native(initial_dir, title)

    if error:
        return jsonify({
            'error': error,
            'selected': False
        }), 500

    return jsonify({
        'path': path,
        'selected': success
    })


# ============================================================
# URL to Markdown Conversion Routes
# ============================================================

@app.route('/check_html_converter')
def check_html_converter():
    """Check if HTML converter is available and dependencies are installed"""
    return jsonify({
        'available': HTML_CONVERTER_AVAILABLE,
        'missing_dependencies': HTML_DEPENDENCIES_MISSING
    })


@app.route('/convert_url', methods=['POST'])
def convert_url():
    """Start URL to Markdown conversion"""
    global url_conversion_status

    if not HTML_CONVERTER_AVAILABLE:
        return jsonify({
            'error': f'HTML converter not available. Missing dependencies: {", ".join(HTML_DEPENDENCIES_MISSING)}'
        }), 400

    data = request.json
    url = data.get('url', '').strip()
    output_folder = data.get('output_folder', 'converted_articles')
    download_images = data.get('download_images', True)

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'URL must start with http:// or https://'}), 400

    # Reset status
    url_conversion_status = {
        'running': True,
        'progress': [],
        'completed': False,
        'success': False,
        'output_file': None,
        'error': None
    }

    # Run conversion in background thread
    def run_url_conversion():
        global url_conversion_status
        try:
            # Capture output
            old_stdout = sys.stdout
            sys.stdout = OutputCapture()

            # Redirect to url_conversion_status instead of conversion_status
            class URLOutputCapture:
                def __init__(self):
                    self.queue = queue.Queue()

                def write(self, text):
                    if text.strip():
                        self.queue.put(text)
                        url_conversion_status['progress'].append(text)
                    sys.__stdout__.write(text)

                def flush(self):
                    sys.__stdout__.flush()

            sys.stdout = URLOutputCapture()

            # Run conversion
            success, message, output_path = convert_url_to_markdown(
                url=url,
                output_dir=output_folder,
                download_images=download_images
            )

            # Restore stdout
            sys.stdout = old_stdout

            url_conversion_status['success'] = success
            url_conversion_status['output_file'] = output_path
            if not success:
                url_conversion_status['error'] = message
            url_conversion_status['completed'] = True
            url_conversion_status['running'] = False

        except Exception as e:
            url_conversion_status['progress'].append(f"Error: {str(e)}")
            url_conversion_status['error'] = str(e)
            url_conversion_status['running'] = False
            url_conversion_status['completed'] = True

    thread = threading.Thread(target=run_url_conversion)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started'})


@app.route('/url_status')
def url_status():
    """Get URL conversion status"""
    return jsonify(url_conversion_status)


# ============================================================
# PDF to Markdown Conversion Routes
# ============================================================

@app.route('/check_pdf_converter')
def check_pdf_converter():
    """Check if PDF converter is available and dependencies are installed"""
    return jsonify({
        'available': PDF_CONVERTER_AVAILABLE,
        'missing_dependencies': PDF_DEPENDENCIES_MISSING
    })


@app.route('/convert_pdf', methods=['POST'])
def convert_pdf():
    """Start PDF to Markdown conversion"""
    global pdf_conversion_status

    if not PDF_CONVERTER_AVAILABLE:
        return jsonify({
            'error': f'PDF converter not available. Missing dependencies: {", ".join(PDF_DEPENDENCIES_MISSING)}'
        }), 400

    data = request.json
    output_folder = data.get('output_folder', 'converted_pdfs')
    accuracy_critical = data.get('accuracy_critical', False)

    # Handle file path from form
    pdf_path = data.get('pdf_path', '').strip()

    if not pdf_path:
        return jsonify({'error': 'PDF file path is required'}), 400

    # Validate file exists
    pdf_path = os.path.expanduser(pdf_path)
    if not os.path.exists(pdf_path):
        return jsonify({'error': f'PDF file not found: {pdf_path}'}), 400

    if not pdf_path.lower().endswith('.pdf'):
        return jsonify({'error': 'File must be a PDF'}), 400

    # Reset status
    pdf_conversion_status = {
        'running': True,
        'progress': [],
        'completed': False,
        'success': False,
        'output_file': None,
        'error': None
    }

    # Run conversion in background thread
    def run_pdf_conversion():
        global pdf_conversion_status
        try:
            # Capture output
            old_stdout = sys.stdout

            class PDFOutputCapture:
                def __init__(self):
                    self.queue = queue.Queue()

                def write(self, text):
                    if text.strip():
                        self.queue.put(text)
                        pdf_conversion_status['progress'].append(text)
                    sys.__stdout__.write(text)

                def flush(self):
                    sys.__stdout__.flush()

            sys.stdout = PDFOutputCapture()

            # Run conversion
            success, message, output_path = convert_pdf_to_markdown(
                pdf_path=pdf_path,
                output_dir=output_folder,
                accuracy_critical=accuracy_critical
            )

            # Restore stdout
            sys.stdout = old_stdout

            pdf_conversion_status['success'] = success
            pdf_conversion_status['output_file'] = output_path
            if not success:
                pdf_conversion_status['error'] = message
            pdf_conversion_status['completed'] = True
            pdf_conversion_status['running'] = False

        except Exception as e:
            pdf_conversion_status['progress'].append(f"Error: {str(e)}")
            pdf_conversion_status['error'] = str(e)
            pdf_conversion_status['running'] = False
            pdf_conversion_status['completed'] = True

    thread = threading.Thread(target=run_pdf_conversion)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started'})


@app.route('/pdf_status')
def pdf_status():
    """Get PDF conversion status"""
    return jsonify(pdf_conversion_status)


@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    """Handle PDF file upload from drag and drop"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided', 'success': False}), 400

    file = request.files['file']
    target_folder = request.form.get('target_folder', '')

    if not file.filename:
        return jsonify({'error': 'No file selected', 'success': False}), 400

    # Validate file extension
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are allowed', 'success': False}), 400

    try:
        # Expand and validate target folder
        if target_folder:
            target_folder = os.path.expanduser(target_folder)
        else:
            # Use temp directory if no target specified
            target_folder = tempfile.gettempdir()

        # Create folder if it doesn't exist
        if not os.path.exists(target_folder):
            os.makedirs(target_folder)

        # Save file
        file_path = os.path.join(target_folder, file.filename)
        file.save(file_path)

        return jsonify({
            'success': True,
            'path': file_path,
            'filename': file.filename
        })

    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500


def main():
    """Start the Flask server"""
    print("=" * 60)
    print("EPUB to Markdown Converter - Web GUI")
    print("=" * 60)
    print()

    # Check Pandoc
    if not check_pandoc_installed():
        print("WARNING: Pandoc is not installed!")
        print("Please install Pandoc from: https://pandoc.org/installing.html")
        print()

    print("Starting web server...")
    print()
    print("Open your browser and navigate to:")
    print("    http://localhost:3763")
    print()
    print("(Port 3763 spells 'EPMD' on a phone - easy to remember!)")
    print()
    print("Press Ctrl+C to stop the server")
    print("=" * 60)

    # Run Flask app
    app.run(debug=False, host='127.0.0.1', port=3763)


if __name__ == '__main__':
    main()
