#!/usr/bin/env python3
"""
EPUB & Web Article to Markdown Converter - Web GUI
A Flask-based web interface for converting EPUBs and web articles to AI-optimized Markdown.
"""

import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from epub_to_md_converter import check_pandoc_installed, process_folder
from version import __version__

# Staging dir for files dropped into the GUI from the user's browser.
# Server-managed so dragged uploads don't pollute the user's chosen folders.
EPUB_STAGING_DIR = os.path.join(tempfile.gettempdir(), 'epub2md_staging')

# Try to import HTML converter (may not be available if dependencies missing)
HTML_CONVERTER_AVAILABLE = False
HTML_DEPENDENCIES_MISSING = []
try:
    from html_to_md_converter import check_dependencies, convert_url_to_markdown
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
    from pdf_to_md_converter import check_dependencies as check_pdf_dependencies
    from pdf_to_md_converter import convert_pdf_to_markdown
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
            with open(PREFERENCES_FILE) as f:
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
    """Capture stdout for progress reporting to a status dict."""
    def __init__(self, status_dict):
        self.queue = queue.Queue()
        self.status_dict = status_dict

    def write(self, text):
        if text.strip():
            self.queue.put(text)
            self.status_dict['progress'].append(text)
        sys.__stdout__.write(text)

    def flush(self):
        sys.__stdout__.flush()


def _pending_self_improve_status():
    """Fresh not-yet-run self-improvement status (a poller waits on this)."""
    return {'running': False, 'progress': [], 'evaluated': 0, 'total': 0,
            'issues_filed': 0, 'engine': None, 'completed': False}


def _pending_rag_status(source):
    """Fresh not-yet-run RAG distill status (a poller waits on this)."""
    return {'running': False, 'progress': [], 'processed': 0, 'total': 0,
            'chunk': 0, 'chunks_total': 0, 'calls': 0,
            'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0,
            'estimate_only': False, 'lifetime_usd': None,
            'source': source, 'completed': False,
            'cancel': False, 'cancelled': False}


# Global state for the self-improvement evaluation (mirrors the conversion status).
self_improvement_status = _pending_self_improve_status()

# Global state for the RAG distillation post-step, one dict per source tab so an
# EPUB batch distill and a PDF distill can never clobber each other's status
# (served by /rag_distill_status?source=epub|pdf).
rag_distill_status = _pending_rag_status('epub')
rag_distill_status_pdf = _pending_rag_status('pdf')


def _finalize_pending_poststeps(source):
    """Terminally complete any post-step status that never ran for this source.

    Called from the conversion thread's `finally`: if a panel was left pending
    (zero converted files, toggle off, or the conversion crashed) its poller
    would otherwise spin forever — or, worse, keep showing a previous run's
    completed cost/results. Mutates in place; never rebinds the globals.
    """
    st = rag_distill_status_pdf if source == 'pdf' else rag_distill_status
    if not st.get('completed') and not st.get('running'):
        st['progress'].append('RAG distill skipped: nothing to distill')
        st['completed'] = True
    if source == 'epub':
        si = self_improvement_status
        if not si.get('completed') and not si.get('running'):
            si['progress'].append('Self-improvement skipped: nothing to evaluate')
            si['completed'] = True


def _run_self_improvement(pairs, model):
    """Judge each converted EPUB and file issues. Self-contained: never raises."""
    global self_improvement_status
    st = _pending_self_improve_status()
    st['running'] = True
    st['total'] = len(pairs)
    self_improvement_status = st

    def log(msg):
        st['progress'].append(str(msg))
        sys.__stdout__.write(str(msg) + "\n")

    try:
        import self_improve
    except Exception as e:
        log(f"Self-improvement unavailable (install the 'selfimprove' extra): {e}")
        st['running'] = False
        st['completed'] = True
        return

    for epub_path, md_path in pairs:
        try:
            result = self_improve.evaluate_conversion(epub_path, md_path, model=model, logger=log)
            if result.get('engine'):
                st['engine'] = result['engine']
            st['evaluated'] += 1
            st['issues_filed'] += int(result.get('filed') or 0)
            log(f"Evaluated {os.path.basename(epub_path)}: {result.get('status')} "
                f"({result.get('filed', 0)} issue(s) filed)")
        except Exception as e:
            log(f"Evaluation error for {os.path.basename(epub_path)}: {e}")

    st['running'] = False
    st['completed'] = True
    log(f"Self-improvement complete: {st['issues_filed']} issue(s) filed.")


class _RunStatusProxy(dict):
    """Per-file `status` dict handed to rag_distill.distill_markdown.

    distill_markdown mirrors the CURRENT file's cumulative usage into the dict
    it is given, overwriting 'calls'/'cost_usd'/token keys after every API call
    — which would reset the live GUI cost badge to per-file spend at each new
    file of a multi-book batch. The proxy stores the per-file values locally
    (so the callee reads back exactly what it wrote) while mirroring
    run-cumulative values — the run-so-far baseline captured at file start plus
    the per-file value — into the run-level status the badge polls.
    Chunk-progress and all other keys pass straight through.
    """

    _COUNTER_KEYS = frozenset({'calls', 'input_tokens', 'output_tokens'})

    def __init__(self, run_status, base_usage):
        super().__init__()
        self._run = run_status
        self._base = {k: int(getattr(base_usage, k, 0) or 0) for k in self._COUNTER_KEYS}
        self._base_cost = base_usage.cost_usd or 0.0
        self._base_estimate = bool(base_usage.estimate_only or base_usage.cost_usd is None)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key in self._COUNTER_KEYS:
            self._run[key] = self._base[key] + int(value or 0)
        elif key == 'cost_usd':
            self._run[key] = round(self._base_cost + float(value or 0.0), 6)
        elif key == 'estimate_only':
            self._run[key] = bool(self._base_estimate or value)
        else:
            self._run[key] = value

    def get(self, key, default=None):
        # The Stop button sets 'cancel' on the RUN status; distill_markdown
        # reads it through this per-file proxy — delegate so the flag is seen.
        if key == 'cancel':
            return self._run.get('cancel', default)
        return super().get(key, default)


def _run_rag_distill(pairs, prefs, source, accuracy_critical):
    """Distill each converted Markdown into a .rag.md companion. Self-contained: never raises."""
    global rag_distill_status, rag_distill_status_pdf
    st = _pending_rag_status(source)
    st['running'] = True
    st['total'] = len(pairs)
    # Rebind the source's global; keep writing through the local `st` so a
    # later run can never receive this run's log lines or counters.
    if source == 'pdf':
        rag_distill_status_pdf = st
    else:
        rag_distill_status = st

    def log(msg):
        st['progress'].append(str(msg))
        sys.__stdout__.write(str(msg) + "\n")

    try:
        import rag_distill
    except Exception as e:
        log(f"RAG distill unavailable: pip install 'epub2md[rag]' ({e})")
        st['running'] = False
        st['completed'] = True
        return

    # Accumulate usage across all files in the run. Each file gets a
    # _RunStatusProxy so the live badge shows run-cumulative spend mid-file;
    # the writes below reconcile the authoritative totals at file boundaries.
    total_usage = rag_distill.UsageTotals()
    for _src_path, md_path in pairs:
        if st.get('cancel'):
            remaining = len(pairs) - st['processed']
            log(f"RAG distill stopped by user — skipping {remaining} remaining file(s)")
            st['cancelled'] = True
            break
        try:
            result = rag_distill.distill_markdown(
                md_path,
                quality=prefs.get('rag_distill_quality', 'standard'),
                accuracy_critical=accuracy_critical,
                cost_cap_usd=float(prefs.get('rag_distill_cost_cap_usd', 2.0)),
                source_kind=source,
                log=log,
                status=_RunStatusProxy(st, total_usage),
            )
            if result.ok:
                st['processed'] += 1
            usage = result.usage
            total_usage.calls += usage.calls
            total_usage.input_tokens += usage.input_tokens
            total_usage.output_tokens += usage.output_tokens
            total_usage.thought_tokens += usage.thought_tokens
            if usage.estimate_only or usage.cost_usd is None:
                total_usage.estimate_only = True
                total_usage.cost_usd = None
            elif total_usage.cost_usd is not None:
                total_usage.cost_usd += usage.cost_usd
            st['calls'] = total_usage.calls
            st['input_tokens'] = total_usage.input_tokens
            st['output_tokens'] = total_usage.output_tokens
            st['cost_usd'] = total_usage.cost_usd if total_usage.cost_usd is not None else 0.0
            st['estimate_only'] = total_usage.estimate_only
            if result.skipped_reason == 'cancelled':
                st['cancelled'] = True
                break                       # don't start the remaining files
        except Exception as e:
            log(f"RAG distill error for {os.path.basename(md_path)}: {e}")

    # Final cost line — the guaranteed surface for "what did this run cost?".
    try:
        lifetime = rag_distill.load_usage_ledger().get('lifetime', {})
        st['lifetime_usd'] = lifetime.get('cost_usd')
        summary = rag_distill.format_usage_line(total_usage, lifetime)
    except Exception:
        summary = f"RAG distill finished: {st['processed']}/{len(pairs)} file(s) distilled."
    log(summary)
    # Mirror into the main conversion log so /status serves the cost line; the
    # frontend re-renders that log when the distill completes, so Copy Logs
    # (which reads the rendered DOM) captures it.
    try:
        target = pdf_conversion_status if source == 'pdf' else conversion_status
        target['progress'].append(summary)
    except Exception:
        pass

    st['running'] = False
    st['completed'] = True


@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html', version=__version__)


@app.route('/check_pandoc')
def check_pandoc():
    """Check if Pandoc is installed"""
    installed = check_pandoc_installed()
    return jsonify({'installed': installed})


def _gather_epub_paths(items):
    """
    Resolve the items list (mix of file paths, folder paths, and staged uploads)
    into a flat list of absolute paths to .epub files.

    Each item is a dict with:
      - kind: 'file' or 'folder'
      - One of: path (absolute path on user's system) or upload_paths (list of
        already-staged absolute paths under EPUB_STAGING_DIR).
    """
    epubs = []
    errors = []

    for item in items:
        path = item.get('path')
        upload_paths = item.get('upload_paths') or []

        if upload_paths:
            for up in upload_paths:
                if os.path.isfile(up) and up.lower().endswith('.epub'):
                    epubs.append(up)
                else:
                    errors.append(f'Staged upload missing or not an EPUB: {up}')
            continue

        if not path:
            errors.append('Item missing both path and upload_paths')
            continue

        path = os.path.expanduser(path)
        if not os.path.exists(path):
            errors.append(f'Path does not exist: {path}')
            continue

        if os.path.isfile(path):
            if path.lower().endswith('.epub'):
                epubs.append(path)
            else:
                errors.append(f'Not an EPUB file: {path}')
        elif os.path.isdir(path):
            for child in sorted(Path(path).glob('*.epub')):
                epubs.append(str(child))

    return epubs, errors


@app.route('/convert', methods=['POST'])
def convert():
    """Start the conversion process from a list of items (files/folders/uploads)."""
    global conversion_status, rag_distill_status, self_improvement_status

    if conversion_status.get('running'):
        return jsonify({'error': 'Another conversion is already running. Wait for it to finish.'}), 409

    data = request.json or {}
    items = data.get('items', [])
    output_folder = data.get('output_folder', 'md processed books')

    if not items:
        return jsonify({'error': 'No input items selected'}), 400

    epub_paths, gather_errors = _gather_epub_paths(items)

    if not epub_paths:
        msg = 'No EPUB files found in selection.'
        if gather_errors:
            msg += ' Details: ' + '; '.join(gather_errors[:3])
        return jsonify({'error': msg}), 400

    # Stage all EPUBs into a fresh temp work dir so process_folder can iterate them
    # uniformly regardless of source.
    work_dir = tempfile.mkdtemp(prefix='epub2md_work_')
    seen_names = set()
    staged_uploads_to_clean = []
    for src in epub_paths:
        base = os.path.basename(src)
        # Disambiguate duplicate filenames coming from different folders
        name = base
        i = 1
        while name in seen_names:
            stem, ext = os.path.splitext(base)
            name = f'{stem} ({i}){ext}'
            i += 1
        seen_names.add(name)
        try:
            shutil.copy2(src, os.path.join(work_dir, name))
            # Drag-drop uploads live under EPUB_STAGING_DIR; remove the staged copy
            # now that it's been duplicated into the work dir. Files outside the
            # staging dir are user-owned originals and must be left alone.
            if os.path.commonpath([os.path.abspath(src), EPUB_STAGING_DIR]) == EPUB_STAGING_DIR:
                staged_uploads_to_clean.append(src)
        except Exception as e:
            print(f'Warning: failed to stage {src}: {e}')

    for src in staged_uploads_to_clean:
        try:
            os.remove(src)
        except OSError:
            pass

    # Reset status
    conversion_status = {
        'running': True,
        'progress': [],
        'current': 0,
        'total': len(epub_paths),
        'completed': False
    }

    # Reset the post-step panels for THIS run, synchronously, before the worker
    # thread can set completed=True: a poller must never observe a previous
    # run's completed state (stale cost/results) or spin on a dict nobody will
    # ever finalize. run_conversion's finally guarantees finalization.
    rag_distill_status = _pending_rag_status('epub')
    self_improvement_status = _pending_self_improve_status()

    if gather_errors:
        for err in gather_errors:
            conversion_status['progress'].append(f'Warning: {err}')

    def run_conversion():
        global conversion_status
        pairs = []
        try:
            old_stdout = sys.stdout
            sys.stdout = OutputCapture(conversion_status)

            pairs = process_folder(work_dir, output_folder)

            sys.stdout = old_stdout

            conversion_status['completed'] = True
            # 'running' stays True through the post-steps (cleared in the
            # finally) so the /convert 409 guard serializes whole runs: a
            # second conversion can't rebind the shared status globals while
            # a distill or evaluation is still writing to them.

            # Post-conversion steps run BEFORE work_dir cleanup (the judge needs
            # the original EPUBs). Each is gated by its toggle and isolated so it
            # can never affect the conversion result. RAG distill runs first:
            # user deliverable before the QA loop.
            prefs = load_preferences()
            if prefs.get('rag_distill_enabled') and pairs:
                try:
                    _run_rag_distill(pairs, prefs, source='epub',
                                     accuracy_critical=bool(prefs.get('rag_accuracy_critical_epub')))
                except Exception as rd_err:
                    print(f"RAG distill error (conversion unaffected): {rd_err}")

            if prefs.get('self_improvement_enabled') and pairs:
                try:
                    _run_self_improvement(pairs, prefs.get('self_improve_model'))
                except Exception as si_err:
                    print(f"Self-improvement error: {si_err}")

        except Exception as e:
            conversion_status['progress'].append(f"Error: {str(e)}")
            conversion_status['completed'] = True

        finally:
            # Resolve any panel whose post-step never ran (no pairs, toggle
            # off, or crash) so its poller terminates cleanly.
            _finalize_pending_poststeps('epub')
            conversion_status['running'] = False
            shutil.rmtree(work_dir, ignore_errors=True)

    thread = threading.Thread(target=run_conversion)
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'started', 'count': len(epub_paths)})


@app.route('/status')
def status():
    """Get conversion status"""
    return jsonify(conversion_status)


@app.route('/self_improve_status')
def self_improve_status():
    """Get self-improvement evaluation status"""
    return jsonify(self_improvement_status)


@app.route('/rag_distill_status')
def get_rag_distill_status():
    """Get RAG distillation status ('epub' tab by default; ?source=pdf for the PDF tab)."""
    source = request.args.get('source', 'epub')
    return jsonify(rag_distill_status_pdf if source == 'pdf' else rag_distill_status)


@app.route('/rag_distill_stop', methods=['POST'])
def rag_distill_stop():
    """Request a running RAG distillation to stop (?source=epub|pdf).

    Sets the cancel flag on the CURRENT run's status dict; rag_distill checks it
    before every API call, between chunks/files, and once per second inside
    retry backoff sleeps, then aborts cleanly (spend recorded, no partial
    companion). Idempotent; a no-op if nothing is running.
    """
    source = request.args.get('source', 'epub')
    st = rag_distill_status_pdf if source == 'pdf' else rag_distill_status
    was_running = bool(st.get('running'))
    st['cancel'] = True
    if was_running:
        st['progress'].append('Stop requested — finishing the in-flight call, no companion will be written…')
    return jsonify({'ok': True, 'was_running': was_running, 'source': source})


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
        'output_folder': prefs.get('output_folder', downloads),
        'url_output_folder': prefs.get('url_output_folder', 'converted_articles'),
        'pdf_output_folder': prefs.get('pdf_output_folder', 'converted_pdfs'),
        'self_improvement_enabled': prefs.get('self_improvement_enabled', False),
        'self_improve_model': prefs.get('self_improve_model', 'claude-opus-4-8'),
        'rag_distill_enabled': prefs.get('rag_distill_enabled', False),
        'rag_distill_enabled_pdf': prefs.get('rag_distill_enabled_pdf', False),
        'rag_distill_quality': prefs.get('rag_distill_quality', 'standard'),
        'rag_accuracy_critical_epub': prefs.get('rag_accuracy_critical_epub', False),
        'has_saved_prefs': bool(prefs)
    })


@app.route('/save_preferences', methods=['POST'])
def save_prefs():
    """Save user preferences"""
    data = request.json
    prefs = load_preferences()

    if 'output_folder' in data:
        prefs['output_folder'] = data['output_folder']
    if 'url_output_folder' in data:
        prefs['url_output_folder'] = data['url_output_folder']
    if 'pdf_output_folder' in data:
        prefs['pdf_output_folder'] = data['pdf_output_folder']
    if 'self_improvement_enabled' in data:
        prefs['self_improvement_enabled'] = bool(data['self_improvement_enabled'])
    if 'self_improve_model' in data:
        prefs['self_improve_model'] = data['self_improve_model']
    if 'rag_distill_enabled' in data:
        prefs['rag_distill_enabled'] = bool(data['rag_distill_enabled'])
    if 'rag_distill_enabled_pdf' in data:
        prefs['rag_distill_enabled_pdf'] = bool(data['rag_distill_enabled_pdf'])
    if 'rag_distill_quality' in data:
        prefs['rag_distill_quality'] = data['rag_distill_quality']
    if 'rag_accuracy_critical_epub' in data:
        prefs['rag_accuracy_critical_epub'] = bool(data['rag_accuracy_critical_epub'])

    success = save_preferences(prefs)
    return jsonify({'success': success})


@app.route('/upload_file', methods=['POST'])
def upload_file():
    """Stage an EPUB upload into a server-managed temp dir.

    Returns the staged absolute path; the client passes that path back in the
    items list at convert time.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided', 'success': False}), 400

    file = request.files['file']

    if not file.filename:
        return jsonify({'error': 'No file selected', 'success': False}), 400

    if not file.filename.lower().endswith('.epub'):
        return jsonify({'error': 'Only EPUB files are allowed', 'success': False}), 400

    try:
        os.makedirs(EPUB_STAGING_DIR, exist_ok=True)

        # Use a uuid prefix to avoid collisions between different drops with the
        # same filename, while keeping the original name visible in the path.
        safe_name = os.path.basename(file.filename)
        staged_name = f'{uuid.uuid4().hex[:8]}_{safe_name}'
        file_path = os.path.join(EPUB_STAGING_DIR, staged_name)
        file.save(file_path)

        return jsonify({
            'success': True,
            'path': file_path,
            'filename': safe_name
        })

    except Exception as e:
        return jsonify({'error': str(e), 'success': False}), 500


def open_folder_dialog_native(initial_dir, title):
    """
    Try to open a native folder dialog using various methods.
    Returns (path, success, error_message)
    """
    import shutil
    import subprocess

    # Ensure initial_dir exists
    initial_dir = os.path.expanduser(initial_dir)
    if not os.path.exists(initial_dir):
        initial_dir = get_downloads_folder()

    is_macos = sys.platform == 'darwin'

    # On macOS, prefer osascript: tkinter must run on the main thread, but Flask
    # handlers run on worker threads, so tk.Tk() from here hangs or crashes the
    # process with an NSInternalInconsistencyException.
    if is_macos and shutil.which('osascript'):
        try:
            script = f'''
            set folderPath to POSIX path of (choose folder with prompt "{title}" default location POSIX file "{initial_dir}")
            return folderPath
            '''
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().rstrip('/'), True, None
            elif result.returncode == 1:
                return '', False, None  # User cancelled
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    # Method 1: Try tkinter (safe on Linux/Windows from threads; skipped on macOS)
    if not is_macos:
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


def open_files_dialog_native(initial_dir, title, extensions):
    """
    Open a native multi-file picker dialog. Returns (paths, success, error).
    `extensions` is a list of lowercase extensions like ['.epub', '.pdf'].
    """
    import shutil as _shutil
    import subprocess

    initial_dir = os.path.expanduser(initial_dir)
    if not os.path.exists(initial_dir):
        initial_dir = get_downloads_folder()

    is_macos = sys.platform == 'darwin'

    # On macOS, prefer osascript: tkinter requires the main thread, but Flask
    # serves requests on worker threads, so tk.Tk() crashes the process there.
    if is_macos and _shutil.which('osascript'):
        try:
            # AppleScript's `of type` clause expects UTIs (Uniform Type Identifiers),
            # not bare extensions. Map known extensions; if any extension isn't
            # mapped, drop the type filter rather than show an empty picker.
            uti_map = {
                '.epub': 'org.idpf.epub-container',
                '.pdf': 'com.adobe.pdf',
            }
            utis = [uti_map.get(ext.lower()) for ext in extensions]
            if all(utis):
                type_clause = ' of type {' + ', '.join(f'"{u}"' for u in utis) + '}'
            else:
                type_clause = ''

            script = f'''
            set theFiles to choose file with prompt "{title}" default location POSIX file "{initial_dir}"{type_clause} with multiple selections allowed
            set output to ""
            repeat with f in theFiles
                set output to output & (POSIX path of f) & linefeed
            end repeat
            return output
            '''
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                # Defensive: enforce extension filter on returned paths in case
                # `of type` was dropped (unknown extension) or the user bypassed it.
                allowed = tuple(ext.lower() for ext in extensions)
                paths = [
                    p for p in result.stdout.strip().split('\n')
                    if p and p.lower().endswith(allowed)
                ]
                if paths:
                    return paths, True, None
                return [], False, None
            elif result.returncode == 1:
                return [], False, None  # Cancelled
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    # Method 1: tkinter (Linux/Windows; skipped on macOS due to main-thread requirement)
    if not is_macos:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            ftypes = [(f'{ext.upper().lstrip(".")} files', f'*{ext}') for ext in extensions]
            ftypes.append(('All files', '*.*'))

            paths = filedialog.askopenfilenames(
                initialdir=initial_dir,
                title=title,
                filetypes=ftypes
            )
            root.destroy()

            if paths:
                return list(paths), True, None
            return [], False, None  # Cancelled
        except ImportError:
            pass
        except Exception:
            pass

    # Method 3: zenity (Linux)
    if _shutil.which('zenity'):
        try:
            ext_filter = ' '.join(f'*{ext}' for ext in extensions)
            result = subprocess.run(
                ['zenity', '--file-selection', '--multiple', '--separator=\n',
                 '--title=' + title, '--filename=' + initial_dir + '/',
                 '--file-filter=' + ext_filter],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0 and result.stdout.strip():
                paths = [p for p in result.stdout.strip().split('\n') if p]
                return paths, True, None
            elif result.returncode == 1:
                return [], False, None
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    return [], False, 'No native dialog tool available (install tkinter, or run on macOS/Linux with zenity)'


@app.route('/native_files_dialog', methods=['POST'])
def native_files_dialog():
    """Open native multi-file picker dialog."""
    data = request.json or {}
    initial_dir = data.get('initial_dir', get_downloads_folder())
    title = data.get('title', 'Select Files')
    extensions = data.get('extensions', ['.epub'])

    paths, success, error = open_files_dialog_native(initial_dir, title, extensions)

    if error:
        return jsonify({'error': error, 'selected': False}), 500

    return jsonify({'paths': paths, 'selected': success})


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
    try:
        page_count = max(1, int(data.get('page_count', 1)))
    except (TypeError, ValueError):
        page_count = 1

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
            sys.stdout = OutputCapture(url_conversion_status)

            # Run conversion
            success, message, output_path = convert_url_to_markdown(
                url=url,
                output_dir=output_folder,
                download_images=download_images,
                page_count=page_count
            )

            # Restore stdout
            sys.stdout = old_stdout

            # Mirror the final outcome into the progress log so it shows up
            # inline (and in Copy Logs), not just in the result banner.
            if success:
                url_conversion_status['progress'].append(f"✓ {message}")
            else:
                url_conversion_status['progress'].append(f"✗ Error: {message}")

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
    global pdf_conversion_status, rag_distill_status_pdf

    if not PDF_CONVERTER_AVAILABLE:
        return jsonify({
            'error': f'PDF converter not available. Missing dependencies: {", ".join(PDF_DEPENDENCIES_MISSING)}'
        }), 400

    if pdf_conversion_status.get('running'):
        return jsonify({'error': 'Another PDF conversion is already running. Wait for it to finish.'}), 409

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

    # Reset the PDF distill panel for THIS run, synchronously, before the
    # worker can set completed=True (same stale-state guarantee as /convert).
    rag_distill_status_pdf = _pending_rag_status('pdf')

    # Run conversion in background thread
    def run_pdf_conversion():
        global pdf_conversion_status
        try:
            # Capture output
            old_stdout = sys.stdout
            sys.stdout = OutputCapture(pdf_conversion_status)

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
            # 'running' stays True through the distill post-step (cleared in
            # the finally) so the /convert_pdf 409 guard serializes whole runs.

            # RAG distill companion (optional post-step). Inner-wrapped so a
            # distill error can never mark the conversion itself as failed.
            prefs = load_preferences()
            if success and output_path and prefs.get('rag_distill_enabled_pdf'):
                try:
                    _run_rag_distill([(pdf_path, output_path)], prefs, source='pdf',
                                     accuracy_critical=accuracy_critical)
                except Exception as rd_err:
                    print(f"RAG distill error (conversion unaffected): {rd_err}")

        except Exception as e:
            pdf_conversion_status['progress'].append(f"Error: {str(e)}")
            pdf_conversion_status['error'] = str(e)
            pdf_conversion_status['completed'] = True

        finally:
            # Resolve the distill panel if its post-step never ran (failed
            # conversion, toggle off, or crash) so its poller terminates.
            _finalize_pending_poststeps('pdf')
            pdf_conversion_status['running'] = False

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


def _sweep_staging_dir():
    """Clear orphaned uploads from prior runs."""
    if os.path.isdir(EPUB_STAGING_DIR):
        try:
            shutil.rmtree(EPUB_STAGING_DIR)
        except OSError:
            pass


def main():
    """Start the Flask server"""
    _sweep_staging_dir()

    print("=" * 60)
    print(f"EPUB to Markdown Converter v{__version__} - Web GUI")
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


def _server_responds(url, timeout=0.5):
    """Return True if something is already serving at url."""
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def run_app_window():
    """Run as a native desktop app: serve Flask in a background thread and show
    the UI in a native WebKit window owned by this process (and thus by the
    epub2md.app bundle, so it gets our icon in the Dock and Cmd+Tab switcher)."""
    import threading
    import time

    import webview  # lazy import: only the .app window mode needs pywebview

    host, port = '127.0.0.1', 3763
    url = f'http://{host}:{port}'

    # Start the server only if it isn't already up (a second launch reuses it
    # instead of crashing on the port bind).
    if not _server_responds(url):
        threading.Thread(
            target=lambda: app.run(
                host=host, port=port, debug=False, use_reloader=False, threaded=True
            ),
            daemon=True,
        ).start()
        for _ in range(120):  # wait up to ~30s for the server to come up
            if _server_responds(url):
                break
            time.sleep(0.25)

    # We run under the framework Python (bundle "Python.app"), so without help the
    # Dock/Cmd+Tab icon and app name would be Python's. Set our name in the app
    # menu, and pass our .icns so pywebview overrides the Dock/switcher icon.
    try:
        from Foundation import NSBundle
        _info = NSBundle.mainBundle().infoDictionary()
        if _info is not None:
            _info['CFBundleName'] = 'epub2md'
    except Exception:
        pass

    icon = os.environ.get('EPUB2MD_ICNS') or None

    # The UI is a fixed 900px-wide card (+20px body padding each side); its height
    # is content-driven. Open the window hidden, size it to fit the whole app once
    # the page has laid out, then reveal it — so it always opens fully visible and
    # never needs manual resizing.
    window = webview.create_window(
        'epub2md', url, width=960, height=1100, min_size=(820, 600), hidden=True
    )

    # Measure the tallest tab so every tab fits without resizing. Each .tab-content
    # is activated in turn (others are display:none), and we take the max container
    # height; +40 accounts for the 20px body padding top and bottom.
    measure_js = (
        "(function(){"
        "var cs=Array.prototype.slice.call(document.querySelectorAll('.tab-content'));"
        "if(!cs.length){return null;}"
        "var active=document.querySelector('.tab-content.active');"
        "var maxH=0;"
        "cs.forEach(function(c){"
        "cs.forEach(function(x){x.classList.remove('active');});"
        "c.classList.add('active');"
        "var h=document.querySelector('.container').getBoundingClientRect().height;"
        "if(h>maxH){maxH=h;}});"
        "cs.forEach(function(x){x.classList.remove('active');});"
        "if(active){active.classList.add('active');}"
        "var cw=document.querySelector('.container').getBoundingClientRect().width;"
        "return [Math.ceil(cw)+40, Math.ceil(maxH)+40];})()"
    )

    shown = {'done': False}

    def _fit_and_show(*_):
        w_px, h_px = 960, 1480  # fallback sized to fit the tallest tab if JS fails
        try:
            dims = window.evaluate_js(measure_js)
            if dims:
                w_px, h_px = int(dims[0]), int(dims[1])
        except Exception:
            pass
        try:  # never grow past the visible screen work area
            scr = webview.screens[0]
            w_px = min(w_px, int(scr.width) - 40)
            h_px = min(h_px, int(scr.height) - 100)
        except Exception:
            pass
        try:
            window.resize(max(w_px, 820), max(h_px, 600))
        except Exception:
            pass
        shown['done'] = True
        window.show()

    window.events.loaded += _fit_and_show

    # Failsafe: reveal the window even if the loaded event never fires.
    def _failsafe_show():
        time.sleep(3)
        if not shown['done']:
            try:
                window.show()
            except Exception:
                pass

    threading.Thread(target=_failsafe_show, daemon=True).start()

    webview.start(icon=icon)  # icon → Dock & Cmd+Tab switcher icon on macOS


if __name__ == '__main__':
    if '--window' in sys.argv:
        run_app_window()
    else:
        main()
