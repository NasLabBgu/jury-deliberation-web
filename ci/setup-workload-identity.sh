#!/bin/bash

# Setup script for Google Cloud Workload Identity Federation with GitHub Actions
# This script sets up keyless authentication between GitHub Actions and Google Cloud

set -e

# Configuration
PROJECT_ID="${1:-your-project-id}"
GITHUB_REPO="${2:-your-org/your-repo}"  # Format: owner/repo
SERVICE_ACCOUNT_NAME="github-actions-deployer"
WORKLOAD_IDENTITY_POOL="github-actions-pool"
WORKLOAD_IDENTITY_PROVIDER="github-actions-provider"
REGION="${3:-us-central1}"

echo "🔧 Setting up Workload Identity Federation for GitHub Actions"
echo "Project ID: $PROJECT_ID"
echo "GitHub Repository: $GITHUB_REPO"
echo "Region: $REGION"

# Check if required parameters are provided
if [ "$PROJECT_ID" = "your-project-id" ] || [ "$GITHUB_REPO" = "your-org/your-repo" ]; then
    echo "❌ Please provide PROJECT_ID and GITHUB_REPO as arguments"
    echo "Usage: $0 PROJECT_ID GITHUB_REPO [REGION]"
    echo "Example: $0 my-project-123 myorg/myrepo us-central1"
    exit 1
fi

# Set the project
echo "📝 Setting Google Cloud project..."
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "🔧 Enabling required APIs..."
gcloud services enable iam.googleapis.com
gcloud services enable iamcredentials.googleapis.com
gcloud services enable sts.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable secretmanager.googleapis.com

# Create Artifact Registry repository
echo "📦 Creating Artifact Registry repository..."
if ! gcloud artifacts repositories describe jury-deliberation-app --location=$REGION >/dev/null 2>&1; then
    gcloud artifacts repositories create jury-deliberation-app \
        --repository-format=docker \
        --location=$REGION \
        --description="Docker repository for jury deliberation app"
    echo "✅ Artifact Registry repository created"
else
    echo "ℹ️  Artifact Registry repository already exists"
fi

# Create service account
echo "👤 Creating service account..."
if ! gcloud iam service-accounts describe $SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com >/dev/null 2>&1; then
    gcloud iam service-accounts create $SERVICE_ACCOUNT_NAME \
        --display-name="GitHub Actions Deployer" \
        --description="Service account for GitHub Actions deployments"
    echo "✅ Service account created"
else
    echo "ℹ️  Service account already exists"
fi

# Grant necessary roles to the service account
echo "🔐 Granting roles to service account..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/run.developer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/storage.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/cloudbuild.builds.builder"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/cloudbuild.builds.editor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/source.admin"

# Create secrets for the application
echo "🔐 Creating application secrets..."
if ! gcloud secrets describe google-api-key >/dev/null 2>&1; then
    echo "Please create the google-api-key secret manually:"
    echo "gcloud secrets create google-api-key"
    echo "echo 'YOUR_GOOGLE_API_KEY' | gcloud secrets versions add google-api-key --data-file=-"
    echo "Visit: https://aistudio.google.com/app/apikey to get your API key"
else
    echo "ℹ️  google-api-key secret already exists"
fi

# Create Workload Identity Pool
echo "🆔 Creating Workload Identity Pool..."
if ! gcloud iam workload-identity-pools describe $WORKLOAD_IDENTITY_POOL --location="global" >/dev/null 2>&1; then
    gcloud iam workload-identity-pools create $WORKLOAD_IDENTITY_POOL \
        --location="global" \
        --display-name="GitHub Actions Pool" \
        --description="Pool for GitHub Actions authentication"
    echo "✅ Workload Identity Pool created"
else
    echo "ℹ️  Workload Identity Pool already exists"
fi

# Create Workload Identity Provider
echo "🔗 Creating Workload Identity Provider..."
if ! gcloud iam workload-identity-pools providers describe $WORKLOAD_IDENTITY_PROVIDER \
    --workload-identity-pool=$WORKLOAD_IDENTITY_POOL \
    --location="global" >/dev/null 2>&1; then
    
    gcloud iam workload-identity-pools providers create-oidc $WORKLOAD_IDENTITY_PROVIDER \
        --workload-identity-pool=$WORKLOAD_IDENTITY_POOL \
        --location="global" \
        --issuer-uri="https://token.actions.githubusercontent.com" \
        --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
        --attribute-condition="assertion.repository=='$GITHUB_REPO'"
    echo "✅ Workload Identity Provider created"
else
    echo "ℹ️  Workload Identity Provider already exists"
fi

# Allow the GitHub Actions runner to impersonate the service account
echo "🔒 Setting up service account impersonation..."
gcloud iam service-accounts add-iam-policy-binding \
    $SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')/locations/global/workloadIdentityPools/$WORKLOAD_IDENTITY_POOL/attribute.repository/$GITHUB_REPO"

# Get the Workload Identity Provider resource name
WIF_PROVIDER="projects/$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')/locations/global/workloadIdentityPools/$WORKLOAD_IDENTITY_POOL/providers/$WORKLOAD_IDENTITY_PROVIDER"
WIF_SERVICE_ACCOUNT="$SERVICE_ACCOUNT_NAME@$PROJECT_ID.iam.gserviceaccount.com"

echo ""
echo "✅ Setup completed successfully!"
echo ""
echo "📋 GitHub Secrets to add:"
echo "========================"
echo "GCP_PROJECT_ID: $PROJECT_ID"
echo "GCP_REGION: $REGION"
echo "WIF_PROVIDER: $WIF_PROVIDER"
echo "WIF_SERVICE_ACCOUNT: $WIF_SERVICE_ACCOUNT"
echo ""
echo "🔧 Next steps:"
echo "1. Add the above secrets to your GitHub repository"
echo "2. Push your code to the main branch to trigger deployment"
echo "3. Monitor the GitHub Actions workflow for successful deployment"
echo ""
echo "📖 To add secrets to GitHub:"
echo "1. Go to your GitHub repository"
echo "2. Settings > Secrets and variables > Actions"
echo "3. Add each secret with the exact name and value shown above"
