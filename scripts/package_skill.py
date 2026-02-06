import os
import sys
import argparse
import yaml
from pathlib import Path

def validate_skill(skill_path):
    path = Path(skill_path)
    if not path.is_dir():
        print(f"Error: '{skill_path}' is not a directory.")
        return False
        
    # 1. Check for required files/folders
    required_layers = ["References", "Scripts", "Assets"]
    skill_md = path / "SKILL.md"
    
    missing = []
    if not skill_md.exists():
        missing.append("SKILL.md")
    for layer in required_layers:
        if not (path / layer).is_dir():
            missing.append(f"{layer}/ directory")
            
    if missing:
        print(f"Validation Failed for '{path.name}': Missing {', '.join(missing)}")
        return False
        
    # 2. Parse SKILL.md YAML Metadata
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.startswith("---"):
                print(f"Validation Failed: '{skill_md.name}' must start with YAML Frontmatter (---)")
                return False
            
            parts = content.split("---")
            if len(parts) < 3:
                print(f"Validation Failed: Invalid YAML Frontmatter in '{skill_md.name}'")
                return False
                
            metadata = yaml.safe_load(parts[1])
            
            # Check name consistency
            if metadata.get("name") != path.name:
                print(f"Validation Failed: Metadata name '{metadata.get('name')}' does not match folder name '{path.name}'")
                return False
                
            # Check required fields
            required_fields = ["name", "provider", "version"]
            for field in required_fields:
                if field not in metadata:
                    print(f"Validation Failed: Missing required metadata field '{field}'")
                    return False
                    
    except Exception as e:
        print(f"Validation Failed: Error parsing YAML - {e}")
        return False
        
    print(f"Validation Success: '{path.name}' is a valid Skill Bundle.")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate an MCP Skill Bundle.")
    parser.add_argument("skill_path", help="Path to the skill bundle directory")
    
    args = parser.parse_args()
    
    # Needs pyyaml for parsing. If not installed, we might need a fallback.
    # But as per instructions, we should handle environment dependencies.
    try:
        if validate_skill(args.skill_path):
            sys.exit(0)
        else:
            sys.exit(1)
    except ImportError:
        print("Error: 'pyyaml' is required for validation. Please install it using 'pip install pyyaml'.")
        sys.exit(1)
