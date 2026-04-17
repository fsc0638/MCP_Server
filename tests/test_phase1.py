"""Integration helper for manually poking the local MCP server.

This file historically lived under tests/ and used pytest naming, but it is not
structured as a real pytest suite (it expects runtime parameters and a running
server). We keep it as an integration helper and skip it in CI/unit runs.
"""

import pytest
import requests
import json
import sys

pytestmark = pytest.mark.integration

# Ensure UTF-8 output for Windows terminal
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE_URL = "http://localhost:8000"


def test_get_models():
    pytest.skip("manual integration helper; requires local server")


def test_chat():
    pytest.skip("manual integration helper; requires local server")


if __name__ == "__main__":
    print("Ensure your MCP Server is running on http://localhost:8000")

    print("=== Testing GET /api/models ===")
    try:
        resp = requests.get(f"{BASE_URL}/api/models")
        print(f"Status: {resp.status_code}")
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Testing POST /chat (Provider: openai, Model: gpt-4o-mini) ===")
    payload = {
        "user_input": "Say 'Hello OpenAI'",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "session_id": "test_openai",
    }

    try:
        resp = requests.post(f"{BASE_URL}/chat", json=payload, stream=True)
        print(f"Status: {resp.status_code}")

        full_content = ""
        for line in resp.iter_lines():
            if line:
                line_text = line.decode("utf-8")
                if line_text.startswith("data: "):
                    data = json.loads(line_text[6:])
                    if data.get("status") == "streaming":
                        content = data.get("content", "")
                        print(content, end="", flush=True)
                        full_content += content
                    elif data.get("status") == "success":
                        print("\n[Final Content Received]")
                        break
                    elif data.get("status") == "error":
                        print(f"\nError Message: {data.get('message')}")
                        break

        if not full_content:
            print("\nWarning: No content received in stream.")

    except Exception as e:
        print(f"\nError: {e}")
