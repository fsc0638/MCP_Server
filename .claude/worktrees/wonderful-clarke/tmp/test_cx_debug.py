import os
import urllib.request
import json
from urllib.error import HTTPError
from dotenv import load_dotenv

load_dotenv()
key = os.getenv('GOOGLE_API_KEY')
cxs = ['267d1ad2394f34a33', '2033882328dfe4c82', '017576662512468393217:u8of38_sy0o']

print(f"Using API Key: {key[:10]}...")

for cx in cxs:
    print(f"\n--- Testing CX: {cx} ---")
    url = f"https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q=test"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            print("Status: 200 OK")
            print("Items found:", len(data.get('items', [])))
            break # If successful, stop
    except HTTPError as e:
        err_data = json.loads(e.read().decode())
        print(f"Status: {e.code}")
        print(f"Message: {err_data['error']['message']}")
