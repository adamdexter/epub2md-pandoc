#!/bin/bash
# Double-click this file in Finder to install (first time), start the
# epub2md GUI server, and open it in your browser.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

URL="http://localhost:3763"

echo "=========================================="
echo "  epub2md — Launcher"
echo "=========================================="
echo ""

# First-run install: if the venv is missing, run the installer.
if [ ! -d ".venv" ]; then
    echo "First-time setup detected. Running installer..."
    echo ""
    bash ./install.sh
    echo ""
fi

# Sanity check the venv exists now.
if [ ! -x ".venv/bin/python3" ]; then
    echo "Error: .venv/bin/python3 not found after install."
    echo "Try running ./install.sh manually in this folder."
    read -n 1 -s -r -p "Press any key to close this window..."
    exit 1
fi

# If something is already serving on the port, just open the browser.
if curl -s -o /dev/null -m 1 "$URL"; then
    echo "epub2md already running at $URL"
    open "$URL"
    echo ""
    echo "You can close this window."
    exit 0
fi

echo "Starting epub2md server at $URL ..."
echo ""

# Launch the GUI in the background so we can open the browser once it's ready.
".venv/bin/python3" gui.py &
SERVER_PID=$!

# Make sure the server gets cleaned up if this terminal window is closed.
trap 'echo ""; echo "Shutting down epub2md..."; kill $SERVER_PID 2>/dev/null; exit 0' INT TERM EXIT

# Wait until the server responds (max ~20s), then open the browser.
for i in {1..40}; do
    if curl -s -o /dev/null -m 1 "$URL"; then
        open "$URL"
        break
    fi
    sleep 0.5
done

echo ""
echo "epub2md is running. Close this Terminal window to stop the server."
echo ""

# Keep the script attached to the server process so closing the window stops it.
wait $SERVER_PID
