import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
model = os.getenv("OPENAI_MODEL", "gpt-4o")

query = "凱衛資訊"

prompt = (
    f"Role: 你現在是一個專業的「網際網路研究專家」。目前的 Google 搜尋系統暫時由你接管，你的任務是針對使用者提出的關鍵字進行深度資源檢索。\n"
    f"Task:\n"
    f"1. 針對使用者的關鍵字：'{query}'，運用你廣大且精確的知識庫進行檢索。\n"
    f"2. 篩選出關聯性最高、最具權威性的 15-20 個真實存在的網頁連結。\n"
    f"3. 嚴格禁止只提供首頁連結（如 www.google.com），必須提供能獲取具體資訊的「深度內頁連結」（如具體的文章、技術論壇、官方新聞稿等）。\n"
    f"Constraints:\n"
    f"- 優先選擇具備長久參考價值的深度資料。\n"
    f"- 排除廣告與無關的社群貼文。\n"
    f"- 確保 URL 格式正確且為可存取的長連結，而非縮網址。\n\n"
    f"Format the output as a JSON array of objects, each containing: "
    f"'title' (string), 'url' (string, valid URL), 'snippet' (string, 1-2 sentences summary), "
    f"and 'favicon' (string, optional URL to a favicon or empty string).\n"
    f"Return ONLY the raw JSON array. DO NOT include markdown code blocks or any other text."
)

print(f"Testing fallback for query: {query}")
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that provides source lists in JSON format. Return only valid JSON."},
            {"role": "user", "content": prompt}
        ]
    )
    content = response.choices[0].message.content.strip()
    print("\n--- LLM Content ---")
    print(content)
    
    match = re.search(r'\[.*\]', content, re.DOTALL)
    json_str = match.group(0) if match else content
    if not match and json_str.startswith("```"):
        json_str = json_str.replace("```json", "", 1).replace("```", "", 1).strip()
    
    print("\n--- Parsed JSON String ---")
    print(json_str)
    
    sources = json.loads(json_str)
    print(f"\nSUCCESS: Parsed {len(sources)} sources.")
except Exception as e:
    print(f"\nERROR: {str(e)}")
