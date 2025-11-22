#!/bin/bash
# EPUB to Markdown Converter - Installer Script
# Automatically installs all dependencies for the converter

set -e  # Exit on error

echo "=========================================="
echo "EPUB to Markdown Converter - Installer"
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

# Install Flask for GUI
install_flask() {
    echo ""
    echo "Installing Flask for GUI..."

    if python3 -c "import flask" &> /dev/null; then
        print_success "Flask is already installed"
    else
        print_info "Installing Flask..."
        python3 -m pip install --user flask
        print_success "Flask installed successfully"
    fi
}

# Make scripts executable
make_executable() {
    echo ""
    echo "Making scripts executable..."

    chmod +x epub_to_md_converter.py
    if [ -f "gui.py" ]; then
        chmod +x gui.py
    fi

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

    # Install Flask
    install_flask

    # Make scripts executable
    make_executable

    echo ""
    echo "=========================================="
    print_success "Installation complete!"
    echo "=========================================="
    echo ""
    echo "You can now use the converter in two ways:"
    echo ""
    echo "1. Command line:"
    echo "   ./epub_to_md_converter.py /path/to/epub/folder"
    echo ""
    echo "2. GUI (recommended):"
    echo "   ./gui.py"
    echo "   Then open http://localhost:5000 in your browser"
    echo ""
}

# Run main installation
main
