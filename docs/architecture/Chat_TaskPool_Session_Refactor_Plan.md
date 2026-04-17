# MCP Server Chat Task Pool and Session Refactor Plan

## Document Status

- Status: Draft for confirmation
- Date: 2026-04-10
- Scope: Web chat session switching, background task execution, approval flow, and task ownership model

---

## 1. Background

The current Web chat flow uses `session_id` as the primary key for multiple responsibilities at once:

- conversation history ownership
- current visible chat window selection
- pending approval lookup
- background task continuation
- post-approval result synthesis

This works for simple single-threaded chat, but it becomes unstable when the user:

- sends a long-running request
- switches to another conversation window
- receives an approval prompt while viewing another conversation
- approves the task from a different window
- continues switching while the original task completes in the background

The result is that UI state, task state, and conversation history state can temporarily drift apart.

---

## 2. Observed Problems

### Problem A: Wrong history rendering during session switch

When the user switches from the original conversation to another window while a task is still running, the newly selected window may fail to show the correct conversation history immediately.

### Problem B: Completed task output appears in the wrong window

When a background task completes while the user is viewing a different conversation, the completion message may temporarily render in the currently visible window instead of staying attached to the original conversation.

### Problem C: Approval flow is session-scoped, not task-scoped

The approval modal and approve/reject routes are bound to `session_id`, not to a distinct background task identity.

This means the system currently models "one pending approval slot per session", not "many independently trackable tasks".

---

## 3. User Flow That Reproduces the Issue

1. User pastes a transcript and asks AI to upload it to Notion.
2. User sends the request.
3. Before approving authorization, user switches to another conversation window.
4. In another conversation window, the authorization prompt appears.
5. User approves from that other window.
6. While the task is still running, user continues switching between windows.
7. A completion message such as "meeting notes uploaded successfully" appears in the currently viewed window, which feels incorrect.
8. After switching back to the original conversation, the display eventually corrects itself.

---

## 4. Review of the Proposed Direction

The proposed direction is correct in principle:

- use one identifier for the currently viewed conversation
- keep running work in a separate task pool
- load conversation history based on the current visible session

However, the implementation should not reuse `session_id` as both the conversation key and the task key.

### Recommended refinement

Split the concepts into three identifiers:

- `currentSessionId`
  - frontend-only concept for "which conversation is currently visible"
- `sessionId`
  - persistent conversation/history ownership key
- `taskId`
  - unique background execution key for long-running requests, approval-required requests, and resumed post-approval tasks

The task pool should therefore be:

- `taskId -> task state`

And each task should carry:

- `sessionId`
- provider/model metadata
- approval status
- partial text
- final text
- error state

This is more robust than trying to split `session_id` into two semantic modes while still reusing the same value.

---

## 5. Current Architecture Findings

### Finding 1: Pending approval storage is keyed only by `session_id`

Evidence:

- `server/core/session.py`
- `_pending_approvals: Dict[str, Dict[str, Any]]`
- `set_pending_approval(session_id, payload)`
- `get_pending_approval(session_id)`
- `clear_pending_approval(session_id)`

Architectural consequence:

- only one approval payload can be safely represented per session
- approval state is not a true task entity
- resume/reject semantics are tied to the conversation key, not to a specific execution

### Finding 2: All adapters write pending approval using `session_id`

Evidence:

- `server/adapters/openai_adapter.py`
- `server/adapters/claude_adapter.py`
- `server/adapters/gemini_adapter.py`

Architectural consequence:

- provider-specific execution state is collapsed into a session-scoped slot
- task identity is lost at the adapter boundary

### Finding 3: Chat routes are session-scoped for history and approval

Evidence:

- `GET /chat/session/{session_id}`
- `POST /chat/approve/{session_id}`
- `POST /chat/reject/{session_id}`

Architectural consequence:

- session loading and task resume share the same addressing model
- frontend cannot distinguish "load this conversation" from "resume that background task"

### Finding 4: Frontend already has a partial task-pool shape

Evidence:

- `frontend/assets/js/chat.js`
- `state.pendingSessions`
- `runTaskInBackground()`
- `onTaskComplete()`
- `onTaskError()`
- `restorePendingSessionUI()`

Architectural consequence:

- the frontend is already moving toward detached background task handling
- but the model is still session-scoped, not task-scoped
- this limits correctness for approval flow, multiple simultaneous tasks, and persistence

### Finding 5: Approval UI is global, not task-bound

Evidence:

- `frontend/assets/js/chat.js`
- `handleApproval()`
- single modal with id `authApprovalModal`

Architectural consequence:

- the visible approval prompt is not a first-class task object
- approval may feel detached from the original conversation context

---

## 6. Target State

### Core principle

Conversation history and background execution must become separate but related models.

### Target model

#### Conversation model

- key: `sessionId`
- content:
  - persisted history
  - title/preview metadata
  - last updated timestamp

#### Task model

- key: `taskId`
- content:
  - `sessionId`
  - `status`
  - `provider`
  - `model`
  - `requiresApproval`
  - `toolName`
  - `partialText`
  - `finalText`
  - `error`
  - `createdAt`
  - `updatedAt`

#### View model

- key: `currentSessionId`
- rendered as:
  - `history(currentSessionId)`
  - plus task overlays whose `task.sessionId === currentSessionId`

---

## 7. Proposed API Direction

### Keep

- `GET /chat/session/{session_id}`
  - only returns persisted conversation history

### Replace or extend

- `POST /chat/approve/{session_id}`
  - should become task-scoped
- `POST /chat/reject/{session_id}`
  - should become task-scoped

### Recommended new task-scoped endpoints

- `POST /chat/tasks/{task_id}/approve`
- `POST /chat/tasks/{task_id}/reject`
- `GET /chat/tasks/{task_id}`
- `GET /chat/tasks?session_id={session_id}`

Optional but recommended:

- `GET /chat/tasks/active`
  - for frontend hydration after reload

---

## 8. Frontend Refactor Direction

### Current direction that should be preserved

- background SSE consumption should remain detached from the visible window
- switching windows should not cancel a running task
- task completion should not force a view switch

### Required frontend changes

#### State model

Replace the session-scoped pending map with a real task pool:

- `currentSessionId`
- `sessions`
- `taskPool`
- `taskIndexBySession`

#### Rendering model

When switching windows:

1. load persisted history by `currentSessionId`
2. fetch or derive task overlays for that same session
3. merge them into the visible read model
4. never let another session's task write directly into the current DOM

#### Approval model

Approval modal should be bound to:

- `taskId`
- `sessionId`
- `toolName`
- `riskDescription`

Not just to a session.

---

## 9. Backend Refactor Direction

### Introduce a task registry

Recommended location:

- `server/core/task_registry.py`

Recommended responsibilities:

- create task records
- update status transitions
- store approval metadata
- store partial/final output
- resolve `taskId -> sessionId`
- list active tasks by session

### Session manager should no longer be the pending-task store

`SessionManager` should remain responsible for:

- conversation history
- metadata related to sessions
- memory flush
- session persistence

It should not be the long-term owner of execution task state.

---

## 10. Task List

### Phase 1: Data model and boundaries

- [ ] Define a new task model with `taskId` separate from `sessionId`
- [ ] Introduce a backend task registry instead of storing pending approvals directly in `SessionManager`
- [ ] Decide task persistence scope: memory-only or persisted on disk under `workspace/tasks/`

### Phase 2: Backend task ownership

- [ ] Replace `_pending_approvals[session_id]` with task-based storage
- [ ] Update OpenAI adapter to create/store task-scoped approval state
- [ ] Update Claude adapter to create/store task-scoped approval state
- [ ] Update Gemini adapter to create/store task-scoped approval state
- [ ] Ensure each provider returns enough metadata for frontend correlation

### Phase 3: Route refactor

- [ ] Keep `GET /chat/session/{session_id}` strictly history-only
- [ ] Add task-scoped approve endpoint
- [ ] Add task-scoped reject endpoint
- [ ] Add task lookup endpoint for task hydration and resume
- [ ] Add session-to-active-tasks lookup endpoint if needed

### Phase 4: Frontend state refactor

- [ ] Replace `pendingSessions` with `taskPool`
- [ ] Maintain `currentSessionId` purely as the current visible window selection
- [ ] Index tasks by `sessionId` for rendering overlays
- [ ] Prevent all DOM writes that are not explicitly scoped to the owning session

### Phase 5: Approval and resume UX

- [ ] Bind approval modal to `taskId`
- [ ] Show which session the approval belongs to
- [ ] Resume approved tasks using task-scoped APIs
- [ ] Ensure approval from a different window still updates the correct session only

### Phase 6: Rendering and history correctness

- [ ] Make session switch render `history + task overlays`
- [ ] Ensure completed task output is attached to the original session only
- [ ] Remove stale task overlays once backend history already contains the final message
- [ ] Handle refresh and re-hydration of in-progress tasks cleanly

### Phase 7: Verification

- [ ] Test switching windows during streaming
- [ ] Test switching windows before authorization
- [ ] Test approving from another window
- [ ] Test switching again while approval-resumed task is running
- [ ] Test completion while viewing a different session
- [ ] Test returning to the original session after completion
- [ ] Test page reload while task is pending or awaiting approval

---

## 11. Risks and Constraints

### Risk 1: Session-scoped hotfixes may mask the real problem

If we continue patching around `session_id` instead of introducing `taskId`, the system may appear stable for single-task usage but remain fragile.

### Risk 2: Frontend-only task pool is not enough

If the task pool exists only in browser memory, task state will be lost on refresh and may still drift from server reality.

### Risk 3: Approval flow is the hardest boundary

Approval introduces a pause-resume lifecycle, so it must be modeled explicitly as task state, not as a UI side effect.

---

## 12. Recommended Execution Order

1. Define task model and registry
2. Refactor backend approval storage and task-scoped APIs
3. Refactor adapters to emit task-scoped metadata
4. Refactor frontend state from `pendingSessions` to `taskPool`
5. Refactor approval modal and resume path
6. Finish rendering merge logic
7. Run regression tests on switch/approval/completion scenarios

---

## 13. Implementation Decision Summary

### Recommended

- use `currentSessionId` for visible conversation selection
- keep `sessionId` as conversation ownership key
- introduce `taskId` as the only background execution key

### Not recommended

- overloading `session_id` into two semantic modes
- storing pending approvals only under session state
- letting background task completion write directly into whichever window is visible

---

## 14. Next Step

After confirmation, implementation should begin with:

- Phase 1: data model definition
- Phase 2: backend task registry

These two phases establish the boundary needed for all later frontend fixes.
