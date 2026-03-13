import os
import sys
import argparse
from pathlib import Path

def create_skill_bundle(provider, tool_name, base_path="skills"):
    # 1. Standardize naming
    folder_name = f"{provider.lower()}-{tool_name.lower().replace('_', '-')}"
    skill_path = Path(base_path) / folder_name
    
    if skill_path.exists():
        print(f"Error: Skill directory '{skill_path}' already exists.")
        sys.exit(1)
        
    # 2. Create directories (4-layer structure)
    layers = ["References", "Scripts", "Assets"]
    skill_path.mkdir(parents=True, exist_ok=True)
    for layer in layers:
        (skill_path / layer).mkdir(exist_ok=True)
        
    # 3. Create SKILL.md template
    skill_md_content = f"""---
name: {folder_name}
provider: {provider}
version: 0.1.0
runtime_requirements:
  - # Add dependencies here (e.g. pandas, python-docx)
description: >
  Provide a detailed description of the skill here. 
  The LLM will use this to decide whether to trigger this skill.
---

# {folder_name.replace('-', ' ').title()}

## Description
[LLM Trigger Decider]
This skill is designed to...

## How to use (Strict Mode / Low Freedom)
- This tool should be called using relative paths to the `Scripts/` directory.
- Input parameters are restricted to:
  - param1 (type): description

## Execution Flow
1. Read Metadata
2. (Optional) Read References
3. Execute Script
4. Process Assets/Templates

## Input Boundary Checking
- [ ] Param check implemented in script
"""
    
    with open(skill_path / "SKILL.md", "w", encoding="utf-8") as f:
        f.write(skill_md_content)
        
    # 4. Create a dummy script template
    script_template = """import sys
import os

# Input Boundary Checking Implementation
def validate_input(args):
    # Example: check if args exist or meet length requirements
    if not args:
        print("Error: Missing arguments")
        sys.exit(1)

def main():
    # Relative path awareness is handled by the MCP Server
    print("Skill executed successfully from Scripts layer")

if __name__ == "__main__":
    main()
"""
    with open(skill_path / "Scripts" / "main.py", "w", encoding="utf-8") as f:
        f.write(script_template)

    print(f"Success: Created Skill Bundle at '{skill_path}'")
    print(f"Structure:")
    print(f"  ├── SKILL.md (Metadata layer)")
    print(f"  ├── References/ (Knowledge layer)")
    print(f"  ├── Scripts/ (Operation layer)")
    print(f"  └── Assets/ (Asset layer)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize an MCP Skill Bundle.")
    parser.add_argument("provider", help="The provider name (e.g., mcp, company)")
    parser.add_argument("tool_name", help="The tool name (e.g., file-processor)")
    
    args = parser.parse_args()
    create_skill_bundle(args.provider, args.tool_name)
