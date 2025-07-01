# Manual Google Cloud Run Deployment Steps

## 1. Prepare your project
```bash
# Set your project ID (replace with your actual project ID)
export PROJECT_ID="your-project-id"
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com
```

## 2. Build and push the Docker image
```bash
# Build the image using Google Cloud Build
gcloud builds submit --tag gcr.io/$PROJECT_ID/jury-deliberation-app

# Alternative: Build locally and push
# docker build -t gcr.io/$PROJECT_ID/jury-deliberation-app .
# docker push gcr.io/$PROJECT_ID/jury-deliberation-app
```

## 3. Deploy to Cloud Run
```bash
gcloud run deploy jury-deliberation-app \
    --image gcr.io/$PROJECT_ID/jury-deliberation-app \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 1 \
    --concurrency 80 \
    --max-instances 10 \
    --timeout 300 \
    --set-env-vars FLASK_ENV=production,PORT=8080,HOST=0.0.0.0
```

## 4. Get the service URL
```bash
gcloud run services describe jury-deliberation-app \
    --region=us-central1 \
    --format='value(status.url)'
```

## 5. Test the deployment
```bash
# Get the URL and test
SERVICE_URL=$(gcloud run services describe jury-deliberation-app --region=us-central1 --format='value(status.url)')
curl "$SERVICE_URL/health"
```

## Common regions to choose from:
- `us-central1` (Iowa)
- `us-east1` (South Carolina)
- `us-west1` (Oregon)
- `europe-west1` (Belgium)
- `asia-east1` (Taiwan)
