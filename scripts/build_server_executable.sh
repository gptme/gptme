#!/bin/bash
set -e

echo "Building gptme-server executable with PyInstaller..."

# Check if we're in the project root
if [ ! -f "pyproject.toml" ]; then
    echo "Error: Must be run from the project root directory"
    exit 1
fi

# Install dependencies including server extras
echo "Installing dependencies..."
poetry install --extras server --with dev

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build/ dist/ scripts/pyinstaller/gptme-server.spec.bak

# Run PyInstaller
echo "Running PyInstaller..."
poetry run pyinstaller scripts/pyinstaller/gptme-server.spec

# Check if build was successful
if [ -f "dist/gptme-server" ] || [ -f "dist/gptme-server.exe" ]; then
    echo "✅ Build successful!"
    echo "Executable location:"
    ls -la dist/gptme-server* 2>/dev/null || true

    echo ""
    echo "Testing the executable..."
    if [ -f "dist/gptme-server" ]; then
        ./dist/gptme-server --help | head -5
    elif [ -f "dist/gptme-server.exe" ]; then
        ./dist/gptme-server.exe --help | head -5
    fi
else
    echo "❌ Build failed - executable not found in dist/"
    exit 1
fi

echo ""
echo "Build complete! You can now distribute the executable from the dist/ directory."
