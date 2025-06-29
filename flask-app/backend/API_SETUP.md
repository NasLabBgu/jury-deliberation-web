# API Key Setup

To use this notebook, you need to create an `api_key` file with your Google API key.

## Steps:

1. Get your Google API key from: https://aistudio.google.com/app/apikey
2. Copy `api_key.template` to `api_key`:
   ```bash
   cp api_key.template api_key
   ```
3. Replace `YOUR_GOOGLE_API_KEY_HERE` in the `api_key` file with your actual API key
4. The `api_key` file is already added to `.gitignore` so it won't be committed to git

## Security Note:
- Never commit your actual API key to version control
- The `api_key` file should contain only your API key and nothing else
- Keep your API key secure and don't share it publicly
