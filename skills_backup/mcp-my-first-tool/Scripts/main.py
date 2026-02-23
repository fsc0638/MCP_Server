import sys
import os
import json
from datetime import datetime


def main():
    name = os.getenv("SKILL_PARAM_NAME", "World")
    message = os.getenv("SKILL_PARAM_MESSAGE", "")

    greeting = f"Hello, {name}!"
    if message:
        greeting += f" You said: '{message}'"

    result = {
        "greeting": greeting,
        "timestamp": datetime.now().isoformat(),
        "status": "success"
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
