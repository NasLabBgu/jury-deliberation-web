#!/bin/bash

# Google Cloud Run Deployment Script
# Prerequisites: 
# 1. Install Google Cloud CLI: https://cloud.google.com/sdk/docs/install
# 2. Run: gcloud auth login
# 3. Run: gcloud config set project YOUR_PROJECT_ID

set -e

# Configuration
PROJECT_ID=${PROJECT_ID:-"your-project-id"}
SERVICE_NAME="jury-deliberation-app"
REGION=${REGION:-"us-central1"}
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "🚀 Deploying Jury Deliberation App to Google Cloud Run"
echo "Project: $PROJECT_ID"
echo "Service: $SERVICE_NAME"
echo "Region: $REGION"
echo "Image: $IMAGE_NAME"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ gcloud CLI is not installed. Please install it first:"
    echo "   https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if logged in
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
    echo "❌ Not logged in to gcloud. Please run: gcloud auth login"
    exit 1
fi

# Set project
echo "📋 Setting project..."
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "🔧 Enabling required APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Build and push Docker image
echo "🏗️  Building Docker image..."
gcloud builds submit --tag $IMAGE_NAME

# Create secrets (if they don't exist)
echo "🔐 Creating secrets..."
if ! gcloud secrets describe app-secrets >/dev/null 2>&1; then
    echo "Creating app-secrets..."
    # You'll need to create these secrets manually or modify this script
    echo "Please create the following secrets in Google Cloud Secret Manager:"
    echo "1. secret-key: A random secret key for Flask"
    echo "2. google-api-key: Your Google AI API key"
    echo ""
    echo "Run these commands:"
    echo "gcloud secrets create app-secrets"
    echo "echo 'your-secret-key-here' | gcloud secrets versions add app-secrets --data-file=-"
    echo ""
    echo "Or create them in the console: https://console.cloud.google.com/security/secret-manager"
fi

# Deploy to Cloud Run
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
    --image $IMAGE_NAME \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --memory 4Gi \
    --cpu 2 \
    --timeout 3600 \
    --max-instances 10 \
    --concurrency 1 \
    --port 8080 \
    --set-env-vars="FLASK_ENV=production,PORT=8080" \
    --set-secrets="SECRET_KEY=app-secrets:1,GOOGLE_API_KEY=app-secrets:2"

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $REGION --format 'value(status.url)')

echo "✅ Deployment completed!"
echo "🌐 Service URL: $SERVICE_URL"
echo "🔍 Health check: $SERVICE_URL/health"
echo ""
echo "📋 Next steps:"
echo "1. Test the health endpoint: curl $SERVICE_URL/health"
echo "2. Visit the app: $SERVICE_URL"
echo "3. Monitor logs: gcloud logs tail /projects/$PROJECT_ID/logs/run.googleapis.com%2Fstdout"
