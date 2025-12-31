#!/usr/bin/env python3
"""
EPUB to Markdown Converter - Web GUI
A simple Flask-based web interface for the EPUB converter.
"""

import os
import sys
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from epub_to_md_converter import process_folder, check_pandoc_installed
import threading
import queue
import io

app = Flask(__name__)

# Global variables for conversion status
conversion_status = {
    'running': False,
    'progress': [],
    'current': 0,
    'total': 0,
    'completed': False
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
