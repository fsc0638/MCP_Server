# Final Migration TODO

## Remaining Scope

1. Optional hardening:
   - replace legacy fallback paths for non-native providers (Gemini/Claude) if you want full native unification.
2. Remove `server/services/prompt_cache.py` dependency on `router.invalidate_prompt_cache`.
3. Remove remaining transitional bridge imports from `router.py`.

## Acceptance Checks

1. `/chat` default OpenAI paths work on native pipeline.
2. Documents/skills updates still invalidate prompt context correctly.
3. Server boots from `server.app:app` with no functional regression on:
   - `/chat`
   - `/skills/*`
   - `/api/documents/*`
   - `/workspace/*`
   - `/tools`, `/resources/*`, `/search/*`
4. `/ui` loads from `frontend` and existing chat interactions remain available.
