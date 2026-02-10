"""
Mode 3: LLM Full-Auto Agent Demo
The LLM automatically decides which tools to call based on user input.

Usage:
  1. Copy .env.template to .env and fill in your API key
  2. Run: python auto_agent.py
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Setup
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from core.uma_core import UMA


def run_openai_agent(uma, user_input: str):
    """Mode 3 with OpenAI GPT"""
    from adapters.openai_adapter import OpenAIAdapter
    adapter = OpenAIAdapter(uma)
    if not adapter.is_available:
        print("[SKIP] OpenAI: API key not set")
        return None
    print(f"\n{'='*50}")
    print(f"[OpenAI GPT] Processing: {user_input}")
    print(f"{'='*50}")
    result = adapter.chat(
        messages=[{"role": "user", "content": user_input}],
        user_query=user_input
    )
    print(f"Status: {result['status']}")
    print(f"Tool calls made: {result.get('tool_calls_made', 0)}")
    print(f"Response:\n{result.get('content', result.get('message', 'No response'))}")
    return result


def run_gemini_agent(uma, user_input: str):
    """Mode 3 with Google Gemini"""
    from adapters.gemini_adapter import GeminiAdapter
    adapter = GeminiAdapter(uma)
    if not adapter.is_available:
        print("[SKIP] Gemini: API key not set")
        return None
    print(f"\n{'='*50}")
    print(f"[Gemini] Processing: {user_input}")
    print(f"{'='*50}")
    result = adapter.chat(user_input)
    print(f"Status: {result['status']}")
    print(f"Tool calls made: {result.get('tool_calls_made', 0)}")
    print(f"Response:\n{result.get('content', result.get('message', 'No response'))}")
    return result


def run_claude_agent(uma, user_input: str):
    """Mode 3 with Anthropic Claude"""
    from adapters.claude_adapter import ClaudeAdapter
    adapter = ClaudeAdapter(uma)
    if not adapter.is_available:
        print("[SKIP] Claude: API key not set")
        return None
    print(f"\n{'='*50}")
    print(f"[Claude] Processing: {user_input}")
    print(f"{'='*50}")
    result = adapter.chat(user_input)
    print(f"Status: {result['status']}")
    print(f"Tool calls made: {result.get('tool_calls_made', 0)}")
    print(f"Response:\n{result.get('content', result.get('message', 'No response'))}")
    return result


def main():
    print("=" * 60)
    print("  Mode 3: LLM Full-Auto Agent")
    print("  LLM decides which tools to call automatically")
    print("=" * 60)

    # Initialize UMA
    uma = UMA(skills_home=os.getenv("SKILLS_HOME", "./skills"))
    uma.initialize()

    print(f"\nRegistered skills: {list(uma.registry.skills.keys())}")

    # Detect available adapters
    available = []
    if os.getenv("OPENAI_API_KEY"):
        available.append("openai")
    if os.getenv("GEMINI_API_KEY"):
        available.append("gemini")
    if os.getenv("ANTHROPIC_API_KEY"):
        available.append("claude")

    if not available:
        print("\n[ERROR] No API keys found in .env file.")
        print("Please copy .env.template to .env and fill in at least one API key:")
        print("  OPENAI_API_KEY=sk-...")
        print("  GEMINI_API_KEY=AI...")
        print("  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    print(f"Available adapters: {available}")

    # Interactive loop
    print("\n--- Enter your request (type 'quit' to exit) ---")
    print("Example prompts:")
    print('  > Convert "hello world" to uppercase')
    print('  > Say hello to Kevin')
    print('  > Count the words in "The quick brown fox jumps over the lazy dog"')
    print()

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        # Use the first available adapter
        model = available[0]
        if model == "openai":
            run_openai_agent(uma, user_input)
        elif model == "gemini":
            run_gemini_agent(uma, user_input)
        elif model == "claude":
            run_claude_agent(uma, user_input)

        print()


if __name__ == "__main__":
    main()
