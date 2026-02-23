import os
import sys
import json
import traceback

def main():
    # The UMA ExecutionEngine injects parameters as SKILL_PARAM_NAME
    code = os.getenv("SKILL_PARAM_CODE", "")
    
    if not code:
        print(json.dumps({"status": "error", "message": "No code provided"}, ensure_ascii=False))
        return

    # To capture print output, we could redirect stdout, 
    # but the simplest way is to just let it print to the console
    # which the ExecutionEngine captures.
    
    try:
        # We use exec() to run the code.
        # We provide a clean global dict but include some common modules.
        # SECURITY WARNING: In a real production system, you'd use a safer sandbox.
        # For this demo, we assume trust.
        
        # Capture stdout
        import io
        from contextlib import redirect_stdout
        
        f = io.StringIO()
        with redirect_stdout(f):
            exec(code)
        
        output = f.getvalue()
        
        print(json.dumps({
            "status": "success",
            "output": output.strip(),
            "message": "Code executed successfully"
        }, ensure_ascii=False))

    except Exception:
        error_msg = traceback.format_exc()
        print(json.dumps({
            "status": "failed",
            "error": error_msg
        }, ensure_ascii=False))

if __name__ == "__main__":
    main()
