import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
CSE_ID = os.getenv("GOOGLE_CSE_ID", "").strip()

print(f"Testing with API_KEY: {API_KEY[:5]}... (len: {len(API_KEY)})")
print(f"Testing with CSE_ID: {CSE_ID} (len: {len(CSE_ID)})")

url = f"https://www.googleapis.com/customsearch/v1"
params = {
    'key': API_KEY,
    'cx': CSE_ID,
    'q': 'Python'
}

try:
    response = requests.get(url, params=params)
    if response.status_code == 200:
        print("SUCCESS: Google Search API is working!")
        data = response.json()
        print(f"Found {len(data.get('items', []))} results.")
    else:
        print(f"FAILED: Status Code {response.status_code}")
        print(f"Response: {response.text}")
except Exception as e:
    print(f"ERROR: {str(e)}")
