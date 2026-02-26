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

    def _strict_json_schema(self, params: Any) -> Any:
        """Recursively convert types to lowercase for strict standard JSON Schema compatibility."""
        if isinstance(params, dict):
            new_params = {}
            for k, v in params.items():
                if k == "type" and isinstance(v, str):
                    new_params[k] = v.lower()
                else:
                    new_params[k] = self._strict_json_schema(v)
            return new_params
        elif isinstance(params, list):
            return [self._strict_json_schema(item) for item in params]
        return params

    def to_openai(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts metadata to OpenAI Tool format.
        D-06: Only uses parameters explicitly defined in metadata.
        Strict JSON Schema: Ensures type strings like "STRING" are converted to "string".
        """
        desc = self.prune_description(metadata.get("description", ""), self.LIMIT_OPENAI)
        
        raw_params = metadata.get("parameters", {
            "type": "object",
            "properties": {},
            "required": []
        })

        # Apply strict lowercase to types
        strict_params = self._strict_json_schema(raw_params)

        return {
            "type": "function",
            "function": {
                "name": metadata.get("name"),
                "description": desc,
                "parameters": strict_params
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
