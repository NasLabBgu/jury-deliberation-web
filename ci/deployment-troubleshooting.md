# Deployment Troubleshooting Guide

## Recent Issues and Solutions

### Issue: IAM Policy Binding Permission Denied (July 2025)

**Problem**: GitHub Actions workflow failing with `PERMISSION_DENIED: Permission 'run.services.setIamPolicy' denied` when trying to set public access on Cloud Run service.

**Root Cause**: The GitHub Actions service account had `roles/run.developer` but not `roles/run.admin`, which is required for IAM policy operations.

**Solution**: 
1. Added `roles/run.admin` to the GitHub Actions service account in setup script
2. Made the public access step conditional (only runs if service isn't already public)
3. The `--allow-unauthenticated` flag in deployment should handle public access automatically

### Issue: Container Startup Failures & Log Streaming Permissions (July 2025)

**Problem**: GitHub Actions workflow was failing with log streaming permission errors when using `gcloud builds log --stream`, and container startup failures were causing traffic to fall back to older working revisions.

**Root Cause**: 
1. The workflow was trying to stream Cloud Build logs but the GitHub Actions service account lacks log viewing permissions
2. Previous attempts used `--async` builds with insufficient wait time, causing deployments of non-existent images

**Solution**: 
1. Use `--async` builds with proper status polling (no log streaming dependency)
2. Poll build status every 30 seconds up to 10 minutes maximum  
3. Handle authentication errors gracefully when checking build status
4. Added image existence verification before deployment
5. Improved health checks with retries and main endpoint testing

### Key Workflow Improvements

#### Build Process
- **Before**: `gcloud builds submit --async` + `sleep 180` OR `gcloud builds log --stream` (fails with permissions)
- **After**: `--async` + status polling every 30s with error handling

#### Deployment Verification
- **Before**: Deploy and hope for the best
- **After**: Verify image exists in registry before deployment

#### Health Checks
- **Before**: Single attempt with 30-second delay
- **After**: 6 retry attempts with both health and main endpoint testing

## Common Commands for Debugging

### Permission Issues
If you encounter log streaming permission errors:
```bash
# This will fail with permissions:
gcloud builds log BUILD_ID --stream

# Use this instead:
gcloud builds describe BUILD_ID --format="value(status)"
```

Note: The GitHub Actions service account has `cloudbuild.builds.builder` and `cloudbuild.builds.viewer` roles, but log streaming requires additional permissions that aren't needed for CI/CD.

### Check Cloud Build Status
```bash
gcloud builds list --limit=5 --format="table(id,status,createTime,duration)"
```

### Check Latest Images
```bash
gcloud artifacts docker images list me-west1-docker.pkg.dev/jury-deliberation-webdemo/jury-deliberation-app/jury-deliberation-app --include-tags --limit=3
```

### Check Cloud Run Service Status
```bash
gcloud run services describe jury-deliberation-app --region=me-west1 --format="yaml(spec.template.spec.containers[0].image,status.traffic)"
```

### Manual Deployment (for testing)
```bash
gcloud run deploy jury-deliberation-app \
  --image me-west1-docker.pkg.dev/jury-deliberation-webdemo/jury-deliberation-app/jury-deliberation-app:TAG \
  --region=me-west1 \
  --platform managed \
  --memory 4Gi \
  --cpu 2 \
  --allow-unauthenticated
```

### Check Container Logs
```bash
gcloud run services logs read jury-deliberation-app --region=me-west1 --limit=50
```

## Service URLs
- **Production**: https://jury-deliberation-app-339604643980.me-west1.run.app
- **Health Check**: https://jury-deliberation-app-339604643980.me-west1.run.app/health

## Architecture
- **Container Registry**: Artifact Registry (me-west1-docker.pkg.dev)
- **Build Service**: Cloud Build
- **Runtime**: Cloud Run (Generation 2)
- **Authentication**: Workload Identity Federation (keyless)
- **Region**: me-west1 (Montreal)

## Service Account Roles
The GitHub Actions service account has these roles:
- `roles/run.developer` - Deploy and manage Cloud Run services
- `roles/run.admin` - Set IAM policies on Cloud Run services
- `roles/artifactregistry.writer` - Push container images
- `roles/cloudbuild.builds.builder` - Submit Cloud Build jobs
- `roles/cloudbuild.builds.editor` - Manage Cloud Build jobs
- `roles/cloudbuild.builds.viewer` - View Cloud Build status
- `roles/storage.admin` - Access Cloud Build storage
- `roles/iam.serviceAccountUser` - Use service accounts
- `roles/secretmanager.secretAccessor` - Access secrets
- `roles/source.admin` - Access source repositories
- `roles/logging.viewer` - View logs (limited use due to VPC-SC)

## Best Practices
1. Always verify build completion before deployment
2. Use health checks with retries for robust verification
3. Monitor both build and deployment logs during CI/CD issues
4. Keep working images tagged for rollback purposes
5. Test manual deployment first when troubleshooting CI/CD issues
