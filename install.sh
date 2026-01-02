#!/bin/bash
# EPUB & Web to Markdown Converter - Installer Script
# Automatically installs all dependencies for the converter

set -e  # Exit on error

echo "=========================================="
echo "EPUB & Web to Markdown Converter - Installer"
echo "=========================================="
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    else
        OS="unknown"
    fi
}

# Check Python installation
check_python() {
    echo "Checking Python installation..."

    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
        print_success "Python 3 is installed (version $PYTHON_VERSION)"
        return 0
    else
        print_error "Python 3 is not installed"
        return 1
    fi
}

# Install Python
install_python() {
    print_info "Installing Python 3..."

    if [ "$OS" == "linux" ]; then
        if command -v apt-get &> /dev/null; then
            sudo apt-get update
            sudo apt-get install -y python3 python3-pip
        elif command -v yum &> /dev/null; then
            sudo yum install -y python3 python3-pip
        elif command -v dnf &> /dev/null; then
            sudo dnf install -y python3 python3-pip
        else
            print_error "Could not detect package manager. Please install Python 3 manually."
            exit 1
        fi
    elif [ "$OS" == "macos" ]; then
        if command -v brew &> /dev/null; then
            brew install python3
        else
            print_error "Homebrew not found. Please install Homebrew first: https://brew.sh"
            exit 1
        fi
    fi

    print_success "Python 3 installed successfully"
}

# Check Pandoc installation
check_pandoc() {
    echo ""
    echo "Checking Pandoc installation..."

    if command -v pandoc &> /dev/null; then
        PANDOC_VERSION=$(pandoc --version | head -n1 | cut -d' ' -f2)
        print_success "Pandoc is installed (version $PANDOC_VERSION)"
        return 0
    else
        print_error "Pandoc is not installed"
        return 1
    fi
}

# Install Pandoc
install_pandoc() {
    print_info "Installing Pandoc..."

    if [ "$OS" == "linux" ]; then
        if command -v apt-get &> /dev/null; then
            sudo apt-get update
            sudo apt-get install -y pandoc
        elif command -v yum &> /dev/null; then
            sudo yum install -y pandoc
        elif command -v dnf &> /dev/null; then
            sudo dnf install -y pandoc
        else
            print_error "Could not detect package manager. Please install Pandoc manually from:"
            print_error "https://pandoc.org/installing.html"
            exit 1
        fi
    elif [ "$OS" == "macos" ]; then
        if command -v brew &> /dev/null; then
            brew install pandoc
        else
            print_error "Homebrew not found. Please install Homebrew first: https://brew.sh"
            exit 1
        fi
    fi

    print_success "Pandoc installed successfully"
}

# Check zenity installation (Linux only - for native folder dialogs)
check_zenity() {
    # Only needed on Linux - macOS uses built-in osascript
    if [ "$OS" != "linux" ]; then
        return 0
    fi

    echo ""
    echo "Checking zenity installation (for native folder dialogs)..."

    if command -v zenity &> /dev/null; then
        print_success "zenity is installed"
        return 0
    else
        print_info "zenity is not installed (optional - enables native folder picker)"
        return 1
    fi
}

# Install zenity
install_zenity() {
    print_info "Installing zenity..."

    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y zenity
    elif command -v yum &> /dev/null; then
        sudo yum install -y zenity
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y zenity
    else
        print_info "Could not install zenity automatically."
        print_info "The GUI will use a built-in folder browser instead."
        return 1
    fi

    print_success "zenity installed successfully"
}

# Create virtual environment and install dependencies
setup_venv() {
    echo ""
    echo "Setting up virtual environment..."

    VENV_DIR=".venv"

    # Check if venv already exists
    if [ -d "$VENV_DIR" ]; then
        print_info "Virtual environment already exists"
    else
        print_info "Creating virtual environment..."

        # Try to install python3-venv if it's not available (for Debian/Ubuntu)
        if [ "$OS" == "linux" ]; then
            if ! python3 -m venv --help &> /dev/null; then
                print_info "Installing python3-venv package..."
                if command -v apt-get &> /dev/null; then
                    sudo apt-get install -y python3-venv
                fi
            fi
        fi

        python3 -m venv "$VENV_DIR"
        print_success "Virtual environment created"
    fi

    # Upgrade pip first
    print_info "Upgrading pip..."
    "$VENV_DIR/bin/pip" install --upgrade pip &> /dev/null

    # Install all dependencies from requirements.txt
    print_info "Installing Python dependencies..."

    if [ -f "requirements.txt" ]; then
        "$VENV_DIR/bin/pip" install -r requirements.txt
        if [ $? -eq 0 ]; then
            print_success "All dependencies installed successfully"
        else
            print_error "Some dependencies failed to install"
            print_info "Trying to install core dependencies individually..."
            "$VENV_DIR/bin/pip" install flask requests trafilatura beautifulsoup4 readability-lxml
            # Medium support dependencies (optional but recommended)
            print_info "Installing Medium article support (Selenium + undetected-chromedriver)..."
            "$VENV_DIR/bin/pip" install selenium webdriver-manager undetected-chromedriver
        fi
    else
        # Fallback if requirements.txt doesn't exist
        "$VENV_DIR/bin/pip" install flask requests trafilatura beautifulsoup4 readability-lxml
        # Medium support dependencies
        print_info "Installing Medium article support..."
        "$VENV_DIR/bin/pip" install selenium webdriver-manager undetected-chromedriver
        if [ $? -eq 0 ]; then
            print_success "All dependencies installed successfully"
        else
            print_error "Failed to install some dependencies"
            print_info "Core functionality will work, but Medium support may be limited"
        fi
    fi
}

# Make scripts executable and create launcher
make_executable() {
    echo ""
    echo "Making scripts executable..."

    chmod +x epub_to_md_converter.py
    if [ -f "html_to_md_converter.py" ]; then
        chmod +x html_to_md_converter.py
    fi
    if [ -f "gui.py" ]; then
        chmod +x gui.py
    fi

    # Create GUI launcher script
    print_info "Creating GUI launcher script..."

    cat > run_gui.sh << 'EOF'
#!/bin/bash
# EPUB to Markdown Converter - GUI Launcher
# This script activates the virtual environment and runs the GUI

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment not found!"
    echo "Please run ./install.sh first"
    exit 1
fi

# Activate virtual environment and run GUI
source .venv/bin/activate
python3 gui.py
EOF

    chmod +x run_gui.sh

    print_success "Scripts are now executable"
}

# Main installation process
main() {
    detect_os

    echo "Detected OS: $OS"
    echo ""

    # Check and install Python
    if ! check_python; then
        read -p "Would you like to install Python 3? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_python
        else
            print_error "Python 3 is required. Exiting."
            exit 1
        fi
    fi

    # Check and install Pandoc
    if ! check_pandoc; then
        read -p "Would you like to install Pandoc? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_pandoc
        else
            print_error "Pandoc is required. Exiting."
            exit 1
        fi
    fi

    # Check and install zenity (Linux only - optional but recommended for native dialogs)
    if ! check_zenity; then
        read -p "Would you like to install zenity for native folder dialogs? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_zenity
        else
            print_info "Skipping zenity - GUI will use built-in folder browser"
        fi
    fi

    # Setup virtual environment and install Flask
    setup_venv

    # Make scripts executable
    make_executable

    echo ""
    echo "=========================================="
    print_success "Installation complete!"
    echo "=========================================="
    echo ""
    echo "You can now use the converter in several ways:"
    echo ""
    echo "1. GUI (recommended):"
    echo "   ./run_gui.sh"
    echo "   Then open http://localhost:3763 in your browser"
    echo "   (Port 3763 = 'EPMD' on phone keypad - easy to remember!)"
    echo ""
    echo "2. Command line - EPUB conversion:"
    echo "   ./epub_to_md_converter.py /path/to/epub/folder"
    echo ""
    echo "3. Command line - Web article conversion:"
    echo "   python3 html_to_md_converter.py https://example.com/article"
    echo ""
}

# Run main installation
main
