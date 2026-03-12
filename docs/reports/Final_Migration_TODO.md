# Final Migration TODO

## Remaining Scope

1. Replace `server/services/chat_service.py` legacy fallback with native implementation in `server/services/chat_core.py` for:
   - `execute=true`
   - `attached_file` flow
2. Remove `server/services/prompt_cache.py` dependency on `router.invalidate_prompt_cache`.
3. Remove remaining transitional bridge imports from `router.py`.

## Acceptance Checks

1. `/chat` default paths work without importing `router.chat` (legacy fallback only for unsupported edge flows).
2. Documents/skills updates still invalidate prompt context correctly.
3. Server boots from `server.app:app` with no functional regression on:
   - `/chat`
   - `/skills/*`
   - `/api/documents/*`
   - `/workspace/*`
   - `/tools`, `/resources/*`, `/search/*`
4. `/ui` loads from `frontend` and existing chat interactions remain available.
