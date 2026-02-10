"""
Mode 3: LLM Full-Auto Agent Demo
The LLM automatically decides which tools to call based on user input.
Supports conversation history (multi-turn) and MEMORY.md persistence.

Usage:
  1. Copy .env.template to .env and fill in your API key
  2. Run: python auto_agent.py
  3. (Optional) Run: python auto_agent.py --model gemini
"""
import os
import sys
import json
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Setup
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from core.uma_core import UMA
from core.session import SessionManager


# --------------- Adapter runners ---------------

def get_openai_adapter(uma):
    from adapters.openai_adapter import OpenAIAdapter
    adapter = OpenAIAdapter(uma)
    if not adapter.is_available:
        print("[SKIP] OpenAI: API key not set or package missing")
        return None
    return adapter


def get_gemini_adapter(uma):
    from adapters.gemini_adapter import GeminiAdapter
    adapter = GeminiAdapter(uma)
    if not adapter.is_available:
        print("[SKIP] Gemini: API key not set or package missing")
        return None
    return adapter


def get_claude_adapter(uma):
    from adapters.claude_adapter import ClaudeAdapter
    adapter = ClaudeAdapter(uma)
    if not adapter.is_available:
        print("[SKIP] Claude: API key not set or package missing")
        return None
    return adapter


ADAPTER_FACTORY = {
    "openai": get_openai_adapter,
    "gemini": get_gemini_adapter,
    "claude": get_claude_adapter,
}


def run_with_adapter(adapter, adapter_name, user_input, conversation_history):
    """Run a single turn with the given adapter, maintaining conversation history."""
    print(f"\n{'='*50}")
    print(f"[{adapter_name}] Processing: {user_input}")
    print(f"{'='*50}")

    try:
        if adapter_name == "openai":
            # OpenAI adapter accepts messages list (multi-turn)
            conversation_history.append({"role": "user", "content": user_input})
            result = adapter.chat(
                messages=list(conversation_history),  # send a copy
                user_query=user_input
            )
        else:
            # Gemini/Claude adapters currently accept single string
            # Pass conversation context via a formatted prompt
            if len(conversation_history) > 0:
                context_lines = []
                for msg in conversation_history[-10:]:  # last 10 turns
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if content:
                        context_lines.append(f"[{role}]: {content}")
                context_lines.append(f"[user]: {user_input}")
                full_prompt = "\n".join(context_lines)
            else:
                full_prompt = user_input
            conversation_history.append({"role": "user", "content": user_input})
            result = adapter.chat(full_prompt)

        if result and result.get("status") == "success":
            # Save assistant response to history
            assistant_content = result.get("content", "")
            conversation_history.append({"role": "assistant", "content": assistant_content})

            print(f"Status: {result['status']}")
            print(f"Tool calls made: {result.get('tool_calls_made', 0)}")
            print(f"Response:\n{assistant_content}")

        return result

    except Exception as e:
        print(f"[ERROR] {adapter_name}: {e}")
        return {"status": "error", "message": str(e)}


# --------------- Main ---------------

def main():
    parser = argparse.ArgumentParser(description="Mode 3: LLM Full-Auto Agent")
    parser.add_argument("--model", choices=["openai", "gemini", "claude"],
                        default=None, help="Force a specific model (default: OpenAI with Gemini fallback)")
    cli_args = parser.parse_args()

    print("=" * 60)
    print("  Mode 3: LLM Full-Auto Agent")
    print("  Conversation history + MEMORY.md enabled")
    print("=" * 60)

    # Initialize UMA
    uma = UMA(skills_home=os.getenv("SKILLS_HOME", "./skills"))
    uma.initialize()

    # Initialize Session Manager
    session_mgr = SessionManager(str(PROJECT_ROOT))
    session_id = session_mgr.create_session("auto_agent")

    print(f"\nRegistered skills: {list(uma.registry.skills.keys())}")
    print(f"Session ID: {session_id}")

    # Determine model priority
    all_models = ["openai", "gemini"]
    if cli_args.model:
        fallbacks = [m for m in all_models if m != cli_args.model]
        model_order = [cli_args.model] + fallbacks
        print(f"Model priority: {cli_args.model} (user specified) -> {' -> '.join(fallbacks)} (fallback)")
    else:
        model_order = all_models
        print("Model priority: OpenAI (default) -> Gemini (fallback)")

    # Resolve adapter once (try in priority order)
    active_adapter = None
    active_adapter_name = None
    for model_name in model_order:
        factory = ADAPTER_FACTORY.get(model_name)
        if factory:
            adapter = factory(uma)
            if adapter:
                active_adapter = adapter
                active_adapter_name = model_name
                print(f"\n[ACTIVE] Using: {model_name}")
                break

    if not active_adapter:
        print("\n[FATAL] No usable model found. Check your .env file.")
        print("  OPENAI_API_KEY=sk-...")
        print("  GEMINI_API_KEY=AI...")
        sys.exit(1)

    # Conversation history (multi-turn memory)
    conversation_history = []
    turn_count = 0

    # Interactive loop
    print("\n--- Enter your request (type 'quit' to exit) ---")
    print("Conversation history is ON -- the LLM remembers your previous messages.")
    print("Example prompts:")
    print('  > Convert "hello world" to uppercase')
    print('  > Say hello to Kevin')
    print('  > What did I just ask you?')
    print()

    try:
        while True:
            user_input = input("You: ").strip()
            if not user_input or user_input.lower() in ("quit", "exit", "q"):
                break

            turn_count += 1
            result = run_with_adapter(
                active_adapter, active_adapter_name,
                user_input, conversation_history
            )

            if result and result.get("status") == "success":
                # Record to session
                session_mgr.record_tool_call(
                    session_id,
                    active_adapter_name,
                    "success",
                    f"Turn {turn_count}: {user_input[:50]}..."
                )
            elif result and result.get("status") == "error":
                # Try fallback
                print(f"[FALLBACK] {active_adapter_name} failed, trying next...")
                for fallback_name in model_order:
                    if fallback_name == active_adapter_name:
                        continue
                    factory = ADAPTER_FACTORY.get(fallback_name)
                    if factory:
                        fb_adapter = factory(uma)
                        if fb_adapter:
                            result = run_with_adapter(
                                fb_adapter, fallback_name,
                                user_input, conversation_history
                            )
                            if result and result.get("status") == "success":
                                # Switch active adapter
                                active_adapter = fb_adapter
                                active_adapter_name = fallback_name
                                print(f"[SWITCHED] Now using: {fallback_name}")
                                session_mgr.record_tool_call(
                                    session_id, fallback_name, "success",
                                    f"Turn {turn_count} (fallback): {user_input[:50]}..."
                                )
                                break

                if result is None or result.get("status") == "error":
                    print("\n[FATAL] All models failed.")
                    break

            print()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    # End session -> sync to MEMORY.md
    summary = f"Auto Agent session with {turn_count} turns using {active_adapter_name}"
    session_mgr.end_session(session_id, summary)
    print(f"\nSession saved to memory/MEMORY.md ({turn_count} turns recorded)")
    print("Goodbye!")


if __name__ == "__main__":
    main()
