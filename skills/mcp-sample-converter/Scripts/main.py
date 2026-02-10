import sys
import os
import json


def main():
    input_text = os.getenv("SKILL_PARAM_INPUT_TEXT", "")
    operation = os.getenv("SKILL_PARAM_OPERATION", "uppercase").lower()

    if not input_text:
        print(json.dumps({"error": "Missing required parameter: input_text"}, ensure_ascii=False))
        sys.exit(1)

    if operation == "uppercase":
        result = input_text.upper()
    elif operation == "lowercase":
        result = input_text.lower()
    elif operation == "titlecase":
        result = input_text.title()
    elif operation == "wordcount":
        words = input_text.split()
        result = json.dumps({
            "total_words": len(words),
            "total_chars": len(input_text),
            "unique_words": len(set(w.lower() for w in words))
        }, ensure_ascii=False)
        print(result)
        return
    else:
        print(json.dumps({"error": f"Unsupported operation: {operation}"}, ensure_ascii=False))
        sys.exit(1)

    print(result)


if __name__ == "__main__":
    main()
