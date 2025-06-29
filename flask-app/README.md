# Jury Deliberation AI App

A production-ready Flask application that simulates jury deliberations using AI agents powered by Google's Gemini API.

## Features

- 🏛️ AI-powered jury deliberation simulation
- 📁 File upload for jury profiles (YAML) and case details (TXT)
- 🎯 Configurable deliberation rounds and parameters
- 📊 Real-time streaming of deliberation process
- 📄 Markdown export of deliberation transcripts
- 🔒 Production-ready security features
- 🐳 Docker containerization
- ☁️ Google Cloud Run deployment ready

## Quick Start

### Local Development

1. **Clone and setup**:
   ```bash
   cd flask-app
   cp api_key.template api_key
   # Edit api_key file with your Google AI API key
   ```

2. **Run with Docker**:
   ```bash
   ./run-local.sh
   ```

3. **Or run with Python**:
   ```bash
   pip install -r requirements-prod.txt
   python app.py
   ```

4. **Access the app**: http://localhost:8080

### Production Deployment to Google Cloud

1. **Prerequisites**:
   - Install [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
   - Create a Google Cloud project
   - Enable billing

2. **Login and setup**:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

3. **Deploy**:
   ```bash
   export PROJECT_ID=your-project-id
   ./deploy-gcp.sh
   ```

## Configuration

### Environment Variables

- `FLASK_ENV`: Set to `production` for production deployment
- `PORT`: Port to run the server on (default: 8080 for Cloud Run)
- `HOST`: Host to bind to (default: 0.0.0.0)
- `SECRET_KEY`: Flask secret key for session security
- `GOOGLE_API_KEY`: Your Google AI API key (can also be in api_key file)

### API Key Setup

Create an `api_key` file in the root directory with your Google AI API key:
```
AIzaSyDfrobVxAAbnVWCzH2zD2JnCjxqJ4LX4ew
```

Get your API key from: https://aistudio.google.com/app/apikey

## API Endpoints

- `GET /` - Main application interface
- `POST /upload` - Upload jury and case files
- `GET /run_notebook` - Start deliberation process
- `GET /health` - Health check endpoint

## File Formats

### Jury Files (YAML)
```yaml
juror_1:
  first_name: "John"
  last_name: "Doe"
  biography: "A retired teacher with 30 years of experience..."
  age: 65
  education: "Master's in Education"
  occupation: "Retired Teacher"
  # ... other fields
```

### Case Files (TXT)
```
Scenario 1:
Background: The defendant is accused of theft...
Evidence: Security footage shows...
Witness testimony: ...
```

## Security Features

- ✅ File type validation (only .yaml, .yml, .txt allowed)
- ✅ Secure filename handling
- ✅ Non-root container user
- ✅ Input sanitization
- ✅ Rate limiting protection
- ✅ Health check endpoints
- ✅ Proper error handling and logging

## Architecture

```
├── app.py                 # Main Flask application
├── backend/               # Notebook and AI logic
│   ├── langgraph_jury_deliberation.ipynb
│   └── api_key           # API key file
├── templates/            # HTML templates
├── Dockerfile           # Container definition
├── requirements-prod.txt # Python dependencies
├── deploy-gcp.sh        # Google Cloud deployment script
└── run-local.sh         # Local development script
```

## Google Cloud Services Used

- **Cloud Run**: Serverless container hosting
- **Cloud Build**: Container image building
- **Container Registry**: Image storage
- **Secret Manager**: Secure API key storage

## Cost Estimation

- **Google Cloud Run**: ~$0.10-1.00 per day for light usage
- **Google AI API**: Free tier: 15 requests/minute, 1,500 requests/day
- **Storage**: Minimal cost for container images

## Troubleshooting

### Common Issues

1. **API Rate Limits**: The app includes automatic retry logic for rate limits
2. **File Upload Issues**: Check file extensions (.yaml, .yml, .txt only)
3. **Memory Issues**: Cloud Run is configured with 4GB RAM for AI processing

### Logs

View logs in Google Cloud:
```bash
gcloud logs tail /projects/PROJECT_ID/logs/run.googleapis.com%2Fstdout
```

Local Docker logs:
```bash
docker logs jury-deliberation-app
```

## Development

### Adding New Features

1. Modify `app.py` for web endpoints
2. Update `backend/langgraph_jury_deliberation.ipynb` for AI logic
3. Rebuild container: `docker build -t jury-deliberation-app .`

### Testing

```bash
# Test health endpoint
curl http://localhost:8080/health

# Test file upload
curl -X POST -F "files=@test.yaml" http://localhost:8080/upload
```

## License

MIT License - see LICENSE file for details

## Support

For issues and support:
1. Check the logs for error messages
2. Verify API key is correctly configured
3. Ensure file formats match the expected structure
4. Check Google Cloud quotas and billing
