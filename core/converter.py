import json
from typing import Dict, Any, List

class SchemaConverter:
    """
    Translates Skill metadata into model-specific function/tool definitions.
    Includes Token Pruning and Logic Injection.
    """
    
    # Model limits for description lengths (approximate)
    LIMIT_GEMINI = 1024
    LIMIT_OPENAI = 1024
    LIMIT_CLAUDE = 1024

    def prune_description(self, description: str, limit: int) -> str:
        """
        Summarizes or truncates description if it exceeds model limits.
        """
        if len(description) <= limit:
            return description
        
        # Simple truncation with indicator for now
        # In a real scenario, we could use a secondary LLM call to summarize
        return description[:limit-50] + "... [Content Pruned for Token Economy]"

    def inject_logic(self, description: str) -> str:
        """
        Injects autonomous logic: 'Resourceful before asking'.
        """
        injection = "\n[SYSTEM NOTE: Before using this tool, prioritize using 'read_resource' or 'search_resource' to check normative references if applicable.]"
        return description + injection

    def to_openai(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts metadata to OpenAI Tool format.
        """
        desc = self.prune_description(metadata.get("description", ""), self.LIMIT_OPENAI)
        desc = self.inject_logic(desc)
        
        # In a real system, we'd parse the 'Input Parameters' section of SKILL.md
        # For this implementation, we assume a standard schema or structured metadata
        return {
            "type": "function",
            "function": {
                "name": metadata.get("name"),
                "description": desc,
                "parameters": metadata.get("parameters", {
                    "type": "object",
                    "properties": {
                        "arguments": {"type": "string", "description": "CLI arguments for the script"}
                    },
                    "required": ["arguments"]
                })
            }
        }

    def to_gemini(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts metadata to Gemini FunctionDeclaration format.
        """
        desc = self.prune_description(metadata.get("description", ""), self.LIMIT_GEMINI)
        desc = self.inject_logic(desc)
        
        return {
            "name": metadata.get("name"),
            "description": desc,
            "parameters": metadata.get("parameters", {
                "type": "OBJECT",
                "properties": {
                    "arguments": {"type": "STRING", "description": "CLI arguments for the script"}
                },
                "required": ["arguments"]
            })
        }

if __name__ == "__main__":
    converter = SchemaConverter()
    sample_meta = {
        "name": "mcp-sample-tool",
        "description": "A very long description that might need pruning... " * 20
    }
    openai_tool = converter.to_openai(sample_meta)
    print(json.dumps(openai_tool, indent=2, ensure_ascii=False))
