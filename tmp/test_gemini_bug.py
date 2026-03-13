import os
import sys
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()

from server.adapters.gemini_adapter import GeminiAdapter

class MockUMA:
    def get_tools_for_model(self, provider):
        return []
    def execute_tool_call(self, name, args):
        return {"status": "success", "result": "mocked"}

uma = MockUMA()
adapter = GeminiAdapter(uma)

# Simulate a multi-modal message history
messages = [
    {"role": "user", "content": [
        {"type": "text", "text": "What is in this image?"},
        {"type": "image_url", "image_url": {"url": "..."}}
    ]}
]

print("Testing GeminiAdapter.chat with multi-modal history...")
try:
    # This should trigger the extraction logic that results in user_query being a list
    gen = adapter.chat(messages=messages)
    for chunk in gen:
        print(chunk)
except Exception as e:
    print(f"Caught expected error: {e}")
    import traceback
    traceback.print_exc()

