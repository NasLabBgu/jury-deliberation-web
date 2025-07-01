# Continuous Integration and Deployment

This directory contains all the configuration and scripts needed for automated deployment to Google Cloud Run using GitHub Actions with keyless authentication.

## 🚀 Overview

The CI/CD pipeline automatically:
1. Builds a Docker container when code is pushed to the `main` branch
2. Pushes the image to Google Artifact Registry
3. Deploys the service to Google Cloud Run
4. Performs health checks to verify the deployment

## 🔐 Keyless Authentication

This setup uses Google Cloud Workload Identity Federation, which eliminates the need to store service account keys in GitHub. This is more secure and follows Google Cloud best practices.

**Benefits:**
- ✅ No long-lived credentials stored in GitHub
- ✅ Automatic credential rotation
- ✅ Fine-grained access control
- ✅ Audit trail of all operations

## 📁 Files in this directory

- `setup-workload-identity.sh` - Script to configure Google Cloud for keyless authentication
- `Dockerfile` - Container definition for the Flask application
- `deploy.sh` - Manual deployment script (for reference)
- `deploy-gcp.sh` - Alternative deployment script
- `cloudrun-service.yaml` - Cloud Run service configuration
- `docker-compose.yml` - Local development configuration
- `.dockerignore` - Files to exclude from Docker build
- `manual-deploy-steps.md` - Documentation for manual deployment

## 🛠️ Setup Instructions

### 1. Prerequisites

- Google Cloud project with billing enabled
- GitHub repository
- `gcloud` CLI installed and authenticated
- Admin access to both Google Cloud project and GitHub repository

### 2. Configure Google Cloud

Run the setup script to configure Workload Identity Federation:

```bash
./ci/setup-workload-identity.sh YOUR_PROJECT_ID your-github-org/your-repo-name us-central1
```

This script will:
- Enable required Google Cloud APIs
- Create a service account with necessary permissions
- Set up Workload Identity Federation
- Create an Artifact Registry repository
- Output the GitHub secrets you need to configure

### 3. Configure GitHub Secrets

Add these secrets to your GitHub repository (Settings > Secrets and variables > Actions):

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `GCP_PROJECT_ID` | Your Google Cloud project ID | `my-project-123` |
| `GCP_REGION` | Deployment region | `us-central1` |
| `WIF_PROVIDER` | Workload Identity Provider | `projects/123456789/locations/global/workloadIdentityPools/...` |
| `WIF_SERVICE_ACCOUNT` | Service account email | `github-actions-deployer@my-project-123.iam.gserviceaccount.com` |

### 4. Test the Pipeline

Push code to the `main` branch or manually trigger the workflow to test the deployment.

## 🔧 Customization

### Environment Variables

Modify the GitHub Actions workflow (`.github/workflows/deploy-to-gce.yml`) to add environment variables:

```yaml
--set-env-vars FLASK_ENV=production,API_KEY=${{ secrets.API_KEY }}
```

### Resource Configuration

Adjust Cloud Run resources in the workflow:

```yaml
--memory 2Gi \
--cpu 1 \
--concurrency 80 \
--max-instances 10 \
--timeout 300
```

### Build Context

The Docker build uses the `flask-app` directory as context:

```yaml
docker build -t ... -f ./ci/Dockerfile ./flask-app
```

## 🐛 Troubleshooting

### Common Issues

1. **Permission Denied**: Ensure the service account has the correct roles
2. **Workload Identity Federation Error**: Verify the provider configuration and GitHub repository name
3. **Artifact Registry Access**: Check that the repository exists and has correct permissions
4. **Health Check Failures**: Ensure your Flask app responds to `/health` endpoint

### Debugging

1. Check GitHub Actions logs for detailed error messages
2. Verify Google Cloud audit logs for permission issues
3. Test authentication locally:
   ```bash
   gcloud auth application-default login
   gcloud config set project YOUR_PROJECT_ID
   ```

### Manual Deployment

If automated deployment fails, you can deploy manually:

```bash
cd ci
./deploy.sh YOUR_PROJECT_ID us-central1
```

## 📚 Additional Resources

- [Google Cloud Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation)
- [GitHub Actions OIDC](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect)
- [Google Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Artifact Registry Documentation](https://cloud.google.com/artifact-registry/docs)

## 🔄 Workflow Triggers

The deployment workflow runs on:
- Push to `main` branch
- Manual trigger via GitHub Actions UI

To modify triggers, edit `.github/workflows/deploy-to-gce.yml`:

```yaml
on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:
```
