import os
import httpx
from dotenv import load_dotenv

load_dotenv("C:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/.env")
api_key = os.environ.get("OPENAI_API_KEY")

def test_api():
    payload = {
        "model": "gpt-4o",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "[系統通知：使用者傳送了一張圖片供您檢視]"},
                ]
            }
        ]
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    with httpx.Client(timeout=30) as client:
        res = client.post("https://api.openai.com/v1/responses", json=payload, headers=headers)
        print("Status:", res.status_code)
        try:
            print("Response:", res.json()['output'][0]['content'][0]['text'])
        except Exception as e:
            print("Error parsing", e)
            print(res.text)

if __name__ == "__main__":
    test_api()
