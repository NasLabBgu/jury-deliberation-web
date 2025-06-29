#!/usr/bin/env python3
"""
Test script to verify API key setup and Google AI connection
"""
import os
import sys

def test_api_key():
    """Test if API key can be loaded"""
    print("Testing API key setup...")
    
    # Try to find the api_key file in multiple possible locations
    possible_paths = [
        'api_key',  # Current directory
        'backend/api_key',  # Backend subdirectory
        os.path.join(os.path.dirname(__file__), 'api_key'),  # Same directory as script
        os.path.join(os.path.dirname(__file__), 'backend', 'api_key'),  # Backend directory relative to script
    ]
    
    api_key = None
    for path in possible_paths:
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    api_key = f.read().strip()
                print(f"✅ API key found at: {path}")
                print(f"   Key preview: {api_key[:10]}...{api_key[-5:]}")
                break
        except Exception as e:
            print(f"❌ Error reading {path}: {e}")
            continue
    
    if api_key is None:
        print("❌ No API key found!")
        print("Searched paths:")
        for path in possible_paths:
            abs_path = os.path.abspath(path)
            exists = "✓" if os.path.exists(path) else "✗"
            print(f"   {exists} {path} -> {abs_path}")
        return False
    
    # Test the API connection
    try:
        print("\n🔍 Testing Google AI connection...")
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash-001",
            temperature=0.3,
            google_api_key=api_key
        )
        
        response = llm.invoke([{"role": "user", "content": "Hello, respond with just 'Connection successful'"}])
        print(f"✅ API connection successful: {response.content}")
        return True
        
    except Exception as e:
        print(f"❌ API connection failed: {e}")
        return False

if __name__ == "__main__":
    print(f"Current working directory: {os.getcwd()}")
    print(f"Script location: {__file__}")
    success = test_api_key()
    sys.exit(0 if success else 1)
