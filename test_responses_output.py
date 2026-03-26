import pytest
import json
from openai import OpenAI
from dotenv import load_dotenv

pytestmark = pytest.mark.integration


def test_responses_output_integration():
    load_dotenv()
    client = OpenAI()

    response = client.responses.create(
    model='gpt-4o',
    input=[
        {'role': 'user', 'content': 'What is the weather in Tokyo?'},
        {
            'role': 'assistant',
            'content': [
                {
                    'type': 'input_item_call',
                    'id': 'call_123',
                    'call': {
                        'name': 'get_weather',
                        'arguments': '{"location":"Tokyo"}'
                    }
                }
            ]
        },
        {
            'role': 'user',
            'content': [
                {
                    'type': 'input_item_call_output',
                    'call_id': 'call_123',
                    'output': 'Sunny, 25C'
                }
            ]
        }
    ]
)
    for chunk in response:
        print(chunk.type)
    print('Done!')
