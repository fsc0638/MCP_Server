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
        """Summarizes or truncates description if it exceeds model limits."""
        if len(description) <= limit:
            return description
        return description[:limit-50] + "... [Content Pruned for Token Economy]"

    def to_openai(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts metadata to OpenAI Tool format.
        D-06: Only uses parameters explicitly defined in metadata.
        """
        desc = self.prune_description(metadata.get("description", ""), self.LIMIT_OPENAI)
        
        return {
            "type": "function",
            "function": {
                "name": metadata.get("name"),
                "description": desc,
                "parameters": metadata.get("parameters", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            }
        }

    def to_gemini(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts metadata to Gemini FunctionDeclaration format.
        D-06: Only uses parameters explicitly defined in metadata.
        """
        desc = self.prune_description(metadata.get("description", ""), self.LIMIT_GEMINI)
        
        return {
            "name": metadata.get("name"),
            "description": desc,
            "parameters": metadata.get("parameters", {
                "type": "OBJECT",
                "properties": {},
                "required": []
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
