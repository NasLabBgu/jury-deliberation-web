#!/bin/bash

# Local Docker Development Script

set -e

echo "🐳 Building and running Jury Deliberation App locally with Docker"

# Build the Docker image
echo "📦 Building Docker image..."
docker build -t jury-deliberation-app .

# Check if api_key file exists
if [ ! -f "api_key" ]; then
    echo "⚠️  Warning: api_key file not found!"
    echo "Please create an api_key file with your Google AI API key"
    echo "You can copy from the template: cp api_key.template api_key"
    echo ""
fi

# Run the container
echo "🚀 Starting container..."
docker run -it --rm \
    -p 8080:8080 \
    -v "$(pwd)/api_key:/app/api_key:ro" \
    --name jury-deliberation-app \
    jury-deliberation-app

echo "✅ Container stopped"
