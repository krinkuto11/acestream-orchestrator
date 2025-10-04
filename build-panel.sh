#!/bin/bash
# Build script for React dashboard

set -e

echo "Building Acestream Orchestrator React Dashboard..."

# Navigate to panel-react directory
cd app/static/panel-react

# Install dependencies if node_modules doesn't exist
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

# Build the React app
echo "Building production bundle..."
npm run build

echo "✓ Dashboard build complete!"
echo "The built files are in app/static/panel/"
