"""
Quick test: Generate a valid LINE signature for a test payload,
then POST it to the local webhook endpoint.

Usage: python scripts/test_line_webhook.py
"""
import hmac
import hashlib
import base64
import os
import sys
from pathlib import Path

# Load .env
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
if not CHANNEL_SECRET:
    # Treat missing secrets as a skip (unit-test friendly). This script is an
    # integration test that requires a local .env.
    print("SKIP: LINE_CHANNEL_SECRET not found in .env")
    raise SystemExit(0)

# Test payload: one TextMessageEvent with LINE v3 required fields
body = '{"destination":"Uc8c4b48b56d3da90c085b8b0e7f6e98a","events":[{"type":"message","message":{"type":"text","id":"123456789","quoteToken":"q3Plxr4AgKd9","text":"早上好"},"timestamp":1741676400000,"source":{"type":"user","userId":"U12345abcde"},"replyToken":"abcdef1234567890abcdef1234567890","mode":"active","webhookEventId":"01HQTEST12345","deliveryContext":{"isRedelivery":false}}]}'

# Generate valid HMAC-SHA256 signature
sig_bytes = hmac.new(
    CHANNEL_SECRET.encode("utf-8"),
    body.encode("utf-8"),
    hashlib.sha256
).digest()
signature = base64.b64encode(sig_bytes).decode("utf-8")

print(f"Generated signature: {signature}")
print(f"Body: {body[:80]}...")
print()

# Send to local server
import urllib.request
import json

req = urllib.request.Request(
    url="https://agentk.ngrok.dev/api/line/webhook",
    data=body.encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-Line-Signature": signature,
        "ngrok-skip-browser-warning": "1",
    },
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"HTTP Status: {resp.status}")
        print(f"Response: {resp.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"HTTP Error: {e.code} — {e.read().decode()}")
except Exception as e:
    print(f"Error: {e}")

print()
print("Check uma_server.log for [LINE BG] entries to see background processing.")
