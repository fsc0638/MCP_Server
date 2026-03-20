import os
import sys
import json
import urllib.request
from urllib.error import HTTPError
from dotenv import load_dotenv

load_dotenv()

key = os.getenv('GOOGLE_API_KEY')
cx = os.getenv('GOOGLE_CSE_ID')
query = 'test'

urls_to_test = [
    f"https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={query}",
    f"https://customsearch.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={query}"
]

print(f"Testing Key: {key[:10]}...")
print(f"Testing CX: {cx}")

for url in urls_to_test:
    print(f"\n--- Testing Endpoint: {url.split('?')[0]} ---")
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req) as response:
            print("Status Code:", response.getcode())
            data = json.loads(response.read().decode())
            print("Success! Items found:", len(data.get('items', [])))
    except HTTPError as e:
        print("Status Code:", e.code)
        body = e.read().decode()
        print("Error Body:", body)
        print("Response Headers:")
        for k, v in e.headers.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print("Unknown Error:", str(e))
