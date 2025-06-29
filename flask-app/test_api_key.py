import os

# Test if GOOGLE_API_KEY is available in the environment
api_key = os.environ.get('GOOGLE_API_KEY')

if api_key:
    print(f"✅ API key is available in environment (length: {len(api_key)})")
    print(f"✅ First 10 characters: {api_key[:10]}...")
else:
    print("❌ No API key found in environment")

# Also test other environment variables
print(f"PORT: {os.environ.get('PORT', 'Not set')}")
print(f"DEPLOYMENT_VERSION: {os.environ.get('DEPLOYMENT_VERSION', 'Not set')}")
