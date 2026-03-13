import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv('.env')

client = OpenAI()
print("Testing Responses API Stateful tracking...")
try:
    # First turn
    resp1 = client.responses.create(
        model='gpt-4o-mini',
        input=[{'role': 'user', 'content': 'My name is Antigravity.'}],
    )
    print("Turn 1 output:", resp1.output[0].content[0].text)
    
    # Second turn
    resp2 = client.responses.create(
        model='gpt-4o-mini',
        previous_response_id=resp1.id,
        input=[{'role': 'user', 'content': 'What is my name?'}],
    )
    print("Turn 2 output:", resp2.output[0].content[0].text)

except Exception as e:
    import traceback
    traceback.print_exc()
