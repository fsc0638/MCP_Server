import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv('.env')

client = OpenAI()
print("Testing Responses API Tool Calling...")
try:
    response = client.responses.create(
        model='gpt-4o-mini',
        input=[{'role': 'user', 'content': 'What is the weather in Tokyo?'}],
        tools=[{
            "type": "function",
            "name": "get_weather",
            "description": "Get the weather in a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }],
        stream=True
    )
    
    for chunk in response:
        print(chunk)
        
except Exception as e:
    import traceback
    traceback.print_exc()
