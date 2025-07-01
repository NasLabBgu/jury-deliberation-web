#!/bin/bash

# Google Cloud Run Deployment Script
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -e

# Configuration
PROJECT_ID=${1:-"your-project-id"}
REGION=${2:-"us-central1"}
SERVICE_NAME="jury-deliberation-app"
IMAGE_TAG="latest"

echo "🚀 Deploying Jury Deliberation App to Google Cloud Run"
echo "Project ID: $PROJECT_ID"
echo "Region: $REGION"
echo "Service Name: $SERVICE_NAME"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ gcloud CLI is not installed. Please install it first."
    exit 1
fi

# Set the project
echo "📝 Setting Google Cloud project..."
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "🔧 Enabling required APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Build and push the Docker image
echo "🏗️  Building Docker image..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME:$IMAGE_TAG

# Create secrets (you'll need to set these manually)
echo "🔐 Creating secrets (if they don't exist)..."
if ! gcloud secrets describe app-secrets >/dev/null 2>&1; then
    echo "Creating app-secrets..."
    echo "Please create secrets manually with:"
    echo "gcloud secrets create app-secrets"
    echo "echo 'your-secret-key' | gcloud secrets versions add app-secrets --data-file=-"
    echo "echo 'your-google-api-key' | gcloud secrets versions add app-secrets --data-file=-"
fi

# Deploy to Cloud Run
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
    --image gcr.io/$PROJECT_ID/$SERVICE_NAME:$IMAGE_TAG \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 1 \
    --concurrency 80 \
    --max-instances 10 \
    --timeout 300 \
    --set-env-vars FLASK_ENV=production

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)')

echo "✅ Deployment completed!"
echo "🌐 Service URL: $SERVICE_URL"
echo "📊 Health check: $SERVICE_URL/health"

# Test the deployment
echo "🧪 Testing deployment..."
if curl -s "$SERVICE_URL/health" > /dev/null; then
    echo "✅ Health check passed!"
else
    echo "❌ Health check failed!"
    exit 1
fi
