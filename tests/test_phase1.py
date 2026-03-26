import pytest
import requests
import json
import sys

pytestmark = pytest.mark.integration

# Ensure UTF-8 output for Windows terminal
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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
    pytest.skip("integration helper script; not a real pytest test")
    print(f"\n=== Testing POST /chat (Provider: {provider}, Model: {model}) ===")
    payload = {
        "user_input": message,
        "provider": provider,
        "model": model,
        "session_id": f"test_{provider}"
    }
    
    try:
        # Note: /chat now returns SSE stream
        resp = requests.post(f"{BASE_URL}/chat", json=payload, stream=True)
        print(f"Status: {resp.status_code}")
        
        full_content = ""
        for line in resp.iter_lines():
            if line:
                line_text = line.decode('utf-8')
                if line_text.startswith("data: "):
                    data = json.loads(line_text[6:])
                    if data.get("status") == "streaming":
                        content = data.get("content", "")
                        print(content, end="", flush=True)
                        full_content += content
                    elif data.get("status") == "success":
                        print(f"\n[Final Content Received]")
                        break
                    elif data.get("status") == "error":
                        print(f"\nError Message: {data.get('message')}")
                        break
        
        if not full_content:
            print("\nWarning: No content received in stream.")
            
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    print("Ensure your MCP Server is running on http://localhost:8000")
    test_get_models()
    
    # Testing standard chat (native path)
    test_chat("openai", "gpt-4o-mini", "Say 'Hello OpenAI'")
    
    # You can enable others if keys are configured
    # test_chat("gemini", "gemini-1.5-flash", "Say 'Hello Gemini'")
