import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
USER_CSE_ID = os.getenv("GOOGLE_CSE_ID", "").strip()
PUBLIC_CSE_ID = "017576662512468393217:u8of38_sy0o" # Google's public sample CX

def test_search(cx_id, label):
    print(f"\n--- Testing {label} (CX: {cx_id}) ---")
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': API_KEY,
        'cx': cx_id,
        'q': 'Google'
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            print(f"SUCCESS: {label} works!")
        else:
            print(f"FAILED: {label} status {response.status_code}")
            print(f"Error Detail: {response.text}")
    except Exception as e:
        print(f"ERROR: {str(e)}")

print(f"Using API_KEY: {API_KEY[:5]}...")
test_search(USER_CSE_ID, "User's CSE ID")
test_search(PUBLIC_CSE_ID, "Public Sample CSE ID")
