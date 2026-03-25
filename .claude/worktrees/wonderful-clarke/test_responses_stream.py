import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv('.env')

client = OpenAI()
print("Testing Responses API Streaming...")
try:
    response = client.responses.create(
        model='gpt-4o-mini',
        input=[{'role': 'user', 'content': 'Tell me a very short joke.'}],
        stream=True
    )
    
    for chunk in response:
        print(chunk)
        
except Exception as e:
    import traceback
    traceback.print_exc()
