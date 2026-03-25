import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv('.env')

client = OpenAI()
print("Testing Responses API...")
try:
    # Minimal call to Responses API
    response = client.responses.create(
        model='gpt-4o-mini',
        input=[{'role': 'user', 'content': 'Say hello world in 2 words.'}],
    )
    
    # Try to see if it's iterable
    try:
        for chunk in response:
            print(chunk)
    except TypeError:
        print("Not iterable. Let's inspect object:")
        print(dir(response))
        if hasattr(response, 'output'):
            print("OUTPUT:", response.output)
        
except Exception as e:
    import traceback
    traceback.print_exc()
