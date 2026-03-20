import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI()

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
}]

print("Turn 1:")
res1 = client.responses.create(
    model='gpt-4o',
    input=[{'role': 'user', 'content': 'What is the weather in Tokyo?'}],
    tools=tools,
    stream=True
)

res1_id = None
call_id = None
for chunk in res1:
    if chunk.type == "response.created":
        res1_id = chunk.response.id
        print("Response ID:", res1_id)
    if chunk.type == "response.output_item.done" and getattr(chunk.item, "type", None) == "function_call":
        call_id = chunk.item.id
        print(f"Tool called: {chunk.item.name} with Args {chunk.item.arguments}")

if call_id and res1_id:
    print("\nTurn 2 (Tool Output):")
    res2 = client.responses.create(
        model='gpt-4o',
        previous_response_id=res1_id,
        input=[
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "Sunny, 25C"
            }
        ],
        stream=True
    )
    for chunk in res2:
        if chunk.type == "response.output_text.delta":
            print(chunk.delta, end="", flush=True)
    print()
