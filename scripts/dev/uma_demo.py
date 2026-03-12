import json
from core.uma_core import UMA

def main():
    print("=== UMA (Unified Model Adapter) Demo ===\n")
    
    # 1. Initialize UMA
    uma = UMA(skills_home="./skills")
    uma.initialize()
    
    # 2. Demonstrate Schema Conversion
    print("--- Model Tool Definitions ---")
    openai_tools = uma.get_tools_for_model("openai")
    print(f"OpenAI Format (first tool):\n{json.dumps(openai_tools[0], indent=2, ensure_ascii=False)}\n")
    
    gemini_tools = uma.get_tools_for_model("gemini")
    print(f"Gemini Format (first tool):\n{json.dumps(gemini_tools[0], indent=2, ensure_ascii=False)}\n")

    # 3. Demonstrate Execution with Context & Security
    print("--- Executing Skill: mcp-sample-converter ---")
    skill_to_run = "mcp-sample-converter"
    result = uma.execute_tool_call(skill_to_run, {"input_file": "data.txt", "mode": "streaming"})
    
    print(f"Execution Status: {result['status']}")
    if result['status'] == 'success':
        print(f"Output: {result['output']}")
    else:
        print(f"Error: {result.get('message', 'Unknown Error')}")
        if 'stderr' in result:
            print(f"Details: {result['stderr']}")

    # 4. Demonstrate Resource Access (Internal logic)
    print("\n--- Testing Resource Access (References) ---")
    res_result = uma.executor.read_resource(skill_to_run, "SKILL.md") # Reading its own SKILL.md as resource
    if res_result['status'] == 'success':
        print(f"Successfully read Reference (first 100 chars): {res_result['content'][:100]}...")

if __name__ == "__main__":
    main()
