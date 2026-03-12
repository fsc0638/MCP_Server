import requests
import json

BASE_URL = "http://localhost:8000"

def test_get_models():
    print("=== Testing GET /api/models ===")
    try:
        resp = requests.get(f"{BASE_URL}/api/models")
        print(f"Status: {resp.status_code}")
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}")

def test_chat(provider, model, message):
    print(f"\n=== Testing POST /chat (Provider: {provider}, Model: {model}) ===")
    payload = {
        "user_input": message,
        "provider": provider,
        "model": model,
        "session_id": f"test_{provider}"
    }
    
    try:
        resp = requests.post(f"{BASE_URL}/chat", json=payload)
        print(f"Status: {resp.status_code}")
        result = resp.json()
        if result.get("status") == "success":
            print(f"Response: {result.get('content')[:100]}...")
        else:
            print(f"Error Message: {result.get('message')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("Ensure your MCP Server is running (e.g. uvicorn main:app --port 8500 --reload)")
    test_get_models()
    
    # You can customize these tests based on your .env configuration
    test_chat("openai", "gpt-4o-mini", "Say 'Hello OpenAI'")
    
    # If GEMINI_API_KEY is set in .env
    test_chat("gemini", "gemini-1.5-flash", "Say 'Hello Gemini'")
    
    # If ANTHROPIC_API_KEY is set in .env
    test_chat("claude", "claude-3-5-sonnet", "Say 'Hello Claude'")
