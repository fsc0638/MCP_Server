(function () {
  "use strict";

  const ACTIVE_TASK_STATUSES = new Set(["running", "tool_call", "requires_approval", "approved"]);
  const TERMINAL_TASK_STATUSES = new Set(["completed", "error", "rejected"]);

  function generatePersistedSessionId() {
    return "web-" + Math.random().toString(36).slice(2, 10);
  }

  function generateDraftSessionId() {
    return "draft-" + Math.random().toString(36).slice(2, 10);
  }

  function isDraftSessionId(sessionId) {
    return String(sessionId || "").startsWith("draft-");
  }

  function generateTurnId() {
    return "turn-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  }

  const state = {
    msgCount: 0,
    tokenCount: 0,
    startAt: Date.now(),
    taskPool: {},
    sessionHistoryCache: {},
    sessionLoadTokens: {},
    models: [{ provider: "openai", model: "gpt-4o", display_name: "OpenAI (gpt-4o)" }],
    modelIndex: 0,
    sessionId: localStorage.getItem("kway_chat_session") || generateDraftSessionId(),
    meetingText: "",
    sessions: JSON.parse(localStorage.getItem("kway_sessions") || "[]"),
    activeApprovalTaskId: null,
    // ── Per-session isolation state ──
    // 每個 session 擁有獨立的 DOM 容器,放在 #chatMessages 內,只以 display 切換
    sessionContainers: {},           // sessionId -> HTMLDivElement
    sessionMsgCounts: {},            // sessionId -> number
    sessionTokenCounts: {},          // sessionId -> number
    sessionMeetingText: {},          // sessionId -> string
    sessionHistoryLoaded: {},        // sessionId -> boolean (避免重複載入)
    sessionInputDrafts: {},          // sessionId -> string (未送出的草稿)
  };
  localStorage.setItem("kway_chat_session", state.sessionId);

  const userData = JSON.parse(
    sessionStorage.getItem("kway_user") ||
      JSON.stringify({
        name: "Workspace User",
        initials: "WU",
        dept: "MCP Workspace",
        email: "",
      })
  );

  const chatMessages = document.getElementById("chatMessages");
  const chatInput = document.getElementById("chatInput");
  const sendBtn = document.getElementById("sendBtn");
  const modelName = document.getElementById("modelName");
  const chatTitleText = document.getElementById("chatTitleText");

  async function hydrateAuthFromServer() {
    try {
      const existing = sessionStorage.getItem("kway_user");
      if (existing) return;

      const res = await fetch("/api/auth/me", { credentials: "include" });
      if (!res.ok) return;

      const data = await res.json();
      if (data && data.status === "success" && data.user && data.user.id) {
        sessionStorage.setItem("kway_user", JSON.stringify(data.user));
        localStorage.setItem("kway_chat_session", data.user.id);
        state.sessionId = data.user.id;
      }
    } catch (_err) {
      // best effort
    }
  }

  function showToast(msg, type) {
    const toast = document.getElementById("toast");
    const toastMsg = document.getElementById("toastMsg");
    const toastIcon = document.getElementById("toastIcon");
    if (!toast || !toastMsg) return;

    toastMsg.textContent = msg;
    toast.className = "toast " + (type || "success");
    if (toastIcon) {
      toastIcon.innerHTML =
        (type || "success") === "error"
          ? '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'
          : '<polyline points="20 6 9 17 4 12"/>';
    }
    toast.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () {
      toast.classList.remove("show");
    }, 3000);
  }
  window.showToast = showToast;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function formatText(text) {
    return escapeHtml(text)
      .replace(/\n/g, "<br>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function getRelativeTimeString(timestamp) {
    if (!timestamp) return "";
    const now = new Date();
    const target = new Date(timestamp);
    const diff = now - target;
    const minutes = Math.floor(diff / 60000);

    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const targetStart = new Date(target.getFullYear(), target.getMonth(), target.getDate());
    const calendarDays = Math.round((todayStart - targetStart) / 86400000);

    if (calendarDays === 0) {
      if (minutes < 1) return "剛剛";
      if (minutes < 60) return minutes + " 分鐘前";
      return Math.floor(diff / 3600000) + " 小時前";
    }
    if (calendarDays === 1) return "昨天";
    if (calendarDays <= 7) return calendarDays + " 天前";
    return (target.getMonth() + 1) + "/" + target.getDate();
  }

  function getDateGroupLabel(timestamp) {
    if (!timestamp) return "較早";
    const now = new Date();
    const target = new Date(timestamp);
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const targetStart = new Date(target.getFullYear(), target.getMonth(), target.getDate());
    const calendarDays = Math.round((todayStart - targetStart) / 86400000);

    if (calendarDays === 0) return "今天";
    if (calendarDays === 1) return "昨天";
    if (calendarDays <= 7) return "最近 7 天";
    if (calendarDays <= 30) return "最近 30 天";
    return target.getFullYear() + "/" + (target.getMonth() + 1);
  }

  function updateStats(extraTokens) {
    if (extraTokens) {
      state.tokenCount += extraTokens;
      state.sessionTokenCounts[state.sessionId] = (state.sessionTokenCounts[state.sessionId] || 0) + extraTokens;
    }
    const statMsgCount = document.getElementById("statMsgCount");
    const statTokens = document.getElementById("statTokens");
    if (statMsgCount) statMsgCount.textContent = String(state.msgCount);
    if (statTokens) statTokens.textContent = String(state.tokenCount);
  }

  // 套用指定 session 的統計數字到右上角 — 切換 session 時呼叫
  function applySessionStats(sessionId) {
    state.msgCount = state.sessionMsgCounts[sessionId] || 0;
    state.tokenCount = state.sessionTokenCounts[sessionId] || 0;
    state.meetingText = state.sessionMeetingText[sessionId] || "";
    updateStats();
  }

  function updateSessionDuration() {
    const statDuration = document.getElementById("statDuration");
    if (!statDuration) return;
    const elapsed = Math.floor((Date.now() - state.startAt) / 1000);
    const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const ss = String(elapsed % 60).padStart(2, "0");
    statDuration.textContent = mm + ":" + ss;
  }

  function saveSessions() {
    localStorage.setItem("kway_sessions", JSON.stringify(state.sessions));
  }

  function nextSessionLoadToken(sessionId) {
    const next = (state.sessionLoadTokens[sessionId] || 0) + 1;
    state.sessionLoadTokens[sessionId] = next;
    return next;
  }

  function isSessionLoadCurrent(sessionId, token) {
    return state.sessionLoadTokens[sessionId] === token;
  }

  function ensureSessionExists(sessionId, title, preview) {
    if (!sessionId || isDraftSessionId(sessionId)) return;
    if (!state.sessions.find(s => s.id === sessionId)) {
      state.sessions.push({
        id: sessionId,
        title: title || "新對話",
        preview: preview || "開始新的對話...",
        timestamp: Date.now(),
      });
      saveSessions();
    }
  }

  function ensureSessionRecord(sessionId, title) {
    if (!sessionId || isDraftSessionId(sessionId)) return;
    if (state.sessions.find(item => item.id === sessionId)) return;
    state.sessions.push({
      id: sessionId,
      title: title || "新對話",
      preview: "",
      timestamp: Date.now(),
    });
    saveSessions();
  }

  function getSessionTitle(sessionId) {
    const session = state.sessions.find(item => item.id === sessionId);
    if (session) return session.title;
    if (isDraftSessionId(sessionId)) return "新對話";
    return sessionId;
  }

  function moveSessionScopedValue(store, fromId, toId, fallback) {
    if (!store) return;
    if (Object.prototype.hasOwnProperty.call(store, fromId)) {
      store[toId] = store[fromId];
      delete store[fromId];
      return;
    }
    if (fallback !== undefined && !Object.prototype.hasOwnProperty.call(store, toId)) {
      store[toId] = fallback;
    }
  }

  function materializeDraftSession(sessionId, seedText) {
    if (!isDraftSessionId(sessionId)) return sessionId;

    let realSessionId = generatePersistedSessionId();
    while (state.sessions.find((item) => item.id === realSessionId)) {
      realSessionId = generatePersistedSessionId();
    }

    const container = state.sessionContainers[sessionId];
    if (container) {
      container.dataset.sessionId = realSessionId;
      state.sessionContainers[realSessionId] = container;
      delete state.sessionContainers[sessionId];
    }

    moveSessionScopedValue(state.sessionMsgCounts, sessionId, realSessionId, 0);
    moveSessionScopedValue(state.sessionTokenCounts, sessionId, realSessionId, 0);
    moveSessionScopedValue(state.sessionMeetingText, sessionId, realSessionId, "");
    moveSessionScopedValue(state.sessionHistoryLoaded, sessionId, realSessionId, false);
    moveSessionScopedValue(state.sessionInputDrafts, sessionId, realSessionId, "");
    moveSessionScopedValue(state.sessionHistoryCache, sessionId, realSessionId, []);
    moveSessionScopedValue(state.sessionLoadTokens, sessionId, realSessionId, 0);

    Object.values(state.taskPool).forEach((task) => {
      if (task && task.sessionId === sessionId) {
        task.sessionId = realSessionId;
      }
    });

    ensureSessionExists(realSessionId, "新對話", "開始新的對話...");

    if (state.sessionId === sessionId) {
      state.sessionId = realSessionId;
      localStorage.setItem("kway_chat_session", realSessionId);
    }

    if (chatTitleText && state.sessionId === realSessionId) {
      chatTitleText.textContent = "新對話";
    }

    if (seedText) {
      updateSessionPreview(realSessionId, seedText);
    } else {
      renderConversationList();
    }

    return realSessionId;
  }

  function getCurrentModel() {
    return state.models[state.modelIndex] || state.models[0];
  }

  function getCurrentModelLabel() {
    const model = getCurrentModel();
    return model.display_name || (model.provider + " (" + model.model + ")");
  }

  function cloneHistoryMessage(msg) {
    if (!msg || typeof msg.content !== "string" || !msg.role) return null;
    return {
      role: msg.role,
      content: msg.content,
      created_at: msg.created_at || null,
    };
  }

  function getCachedHistory(sessionId) {
    const cached = state.sessionHistoryCache[sessionId];
    if (!Array.isArray(cached)) return [];
    return cached.map(cloneHistoryMessage).filter(Boolean);
  }

  function setCachedHistory(sessionId, history) {
    state.sessionHistoryCache[sessionId] = (Array.isArray(history) ? history : [])
      .map(cloneHistoryMessage)
      .filter(Boolean);
  }

  function getHistorySignature(msg) {
    const normalized = cloneHistoryMessage(msg);
    if (!normalized) return "";
    return normalized.role + "\u0000" + normalized.content;
  }

  function mergeHistoryWithCache(sessionId, history) {
    const remoteHistory = (Array.isArray(history) ? history : [])
      .map(cloneHistoryMessage)
      .filter(Boolean);

    if (remoteHistory.length === 0) {
      return getCachedHistory(sessionId);
    }

    const cachedHistory = getCachedHistory(sessionId);
    if (cachedHistory.length === 0) {
      setCachedHistory(sessionId, remoteHistory);
      return remoteHistory;
    }

    const remoteCounts = Object.create(null);
    remoteHistory.forEach((msg) => {
      const key = getHistorySignature(msg);
      if (!key) return;
      remoteCounts[key] = (remoteCounts[key] || 0) + 1;
    });

    const cachedSeen = Object.create(null);
    const merged = remoteHistory.slice();

    cachedHistory.forEach((msg) => {
      const key = getHistorySignature(msg);
      if (!key) return;
      cachedSeen[key] = (cachedSeen[key] || 0) + 1;
      if (cachedSeen[key] > (remoteCounts[key] || 0)) {
        merged.push(msg);
      }
    });

    setCachedHistory(sessionId, merged);
    return merged;
  }

  function appendCachedHistoryMessage(sessionId, role, content, createdAt) {
    if (!sessionId || typeof content !== "string") return;
    const current = getCachedHistory(sessionId);
    current.push({
      role: role,
      content: content,
      created_at: createdAt || Math.floor(Date.now() / 1000),
    });
    setCachedHistory(sessionId, current);
  }

  // ── Per-session DOM container management ──
  // 每個 session 有自己的 <div class="page-chat-session-container"> 放在 #chatMessages 底下
  // 切換只改 display,不清空內容,讓背景 task 的 DOM 更新永遠指向正確的容器
  function getSessionContainer(sessionId) {
    if (!sessionId || !chatMessages) return null;
    let container = state.sessionContainers[sessionId];
    if (container && chatMessages.contains(container)) return container;

    container = document.createElement("div");
    container.className = "page-chat-session-container";
    container.dataset.sessionId = sessionId;
    container.style.display = sessionId === state.sessionId ? "" : "none";
    container.style.width = "100%";
    chatMessages.appendChild(container);
    state.sessionContainers[sessionId] = container;
    return container;
  }

  function showSessionContainer(sessionId) {
    if (!chatMessages) return;
    Object.keys(state.sessionContainers).forEach((sid) => {
      const el = state.sessionContainers[sid];
      if (!el) return;
      el.style.display = sid === sessionId ? "" : "none";
    });
    // 確保目標 session 的容器存在
    getSessionContainer(sessionId);
  }

  function clearSessionContainer(sessionId) {
    const container = state.sessionContainers[sessionId];
    if (container) container.innerHTML = "";
    state.sessionMsgCounts[sessionId] = 0;
    state.sessionTokenCounts[sessionId] = 0;
    state.sessionMeetingText[sessionId] = "";
  }

  function removeSessionContainer(sessionId) {
    const container = state.sessionContainers[sessionId];
    if (container && container.parentNode) container.parentNode.removeChild(container);
    delete state.sessionContainers[sessionId];
    delete state.sessionMsgCounts[sessionId];
    delete state.sessionTokenCounts[sessionId];
    delete state.sessionMeetingText[sessionId];
    delete state.sessionHistoryLoaded[sessionId];
    delete state.sessionInputDrafts[sessionId];
    delete state.sessionLoadTokens[sessionId];
  }

  function scrollSessionToBottom(sessionId) {
    if (!chatMessages) return;
    if (sessionId !== state.sessionId) return; // 非可見 session 不捲動
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function renderConversationPlaceholder(sessionId, title, body) {
    const container = getSessionContainer(sessionId);
    if (!container) return;
    container.innerHTML =
      '<div class="page-chat-welcome" id="chatWelcome-' + String(sessionId).replace(/[^a-zA-Z0-9_-]/g, "_") + '">' +
      '<div class="page-chat-welcome-logo"><img src="../assets/images/kw_logo.png" width="56" alt="Logo"></div>' +
      '<h2>' + escapeHtml(title) + '</h2>' +
      '<p>' + escapeHtml(body) + '</p>' +
      '</div>';
    state.sessionMsgCounts[sessionId] = 0;
    state.sessionTokenCounts[sessionId] = 0;
    state.sessionMeetingText[sessionId] = "";
  }

  function renderHistoryMessages(sessionId, history) {
    clearSessionContainer(sessionId);
    if (!Array.isArray(history) || history.length === 0) return false;

    let msgCount = 0;
    let tokenCount = 0;
    let meetingText = "";

    history.forEach((msg) => {
      const normalized = cloneHistoryMessage(msg);
      if (!normalized) return;
      const role = normalized.role === "assistant" ? "ai" : "user";
      renderMessage(sessionId, role, normalized.content, normalized.created_at ? normalized.created_at * 1000 : null);
      msgCount += 1;
      tokenCount += Math.ceil(normalized.content.length / 4);
      const speaker = role === "ai" ? "Assistant" : "User";
      meetingText += (meetingText ? "\n\n" : "") + speaker + ":\n" + normalized.content;
    });

    state.sessionMsgCounts[sessionId] = msgCount;
    state.sessionTokenCounts[sessionId] = tokenCount;
    state.sessionMeetingText[sessionId] = meetingText;

    if (sessionId === state.sessionId) {
      state.msgCount = msgCount;
      state.tokenCount = tokenCount;
      state.meetingText = meetingText;
      updateStats();
    }
    return true;
  }

  function dismissApprovalModal(resetPrompt) {
    const modal = document.getElementById("authApprovalModal");
    if (modal) modal.remove();

    const taskId = state.activeApprovalTaskId;
    state.activeApprovalTaskId = null;

    if (!resetPrompt || !taskId) return;
    const task = state.taskPool[taskId];
    if (task && task.status === "requires_approval") {
      task.approvalPrompted = false;
    }
  }

  function isActiveTaskStatus(status) {
    return ACTIVE_TASK_STATUSES.has(status || "");
  }

  function isTerminalTaskStatus(status) {
    return TERMINAL_TASK_STATUSES.has(status || "");
  }

  function generateLocalTaskId() {
    return "local-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  }

  function createTaskState(sessionId, taskId, turnId) {
    return {
      taskId: taskId,
      sessionId: sessionId,
      turnId: turnId || "",
      localOnly: String(taskId || "").startsWith("local-"),
      status: "running",
      text: "",
      error: "",
      bubbleEl: null,
      firstChunkReceived: false,
      completed: false,
      toolName: "",
      toolMessage: "",
      riskDescription: "",
      pendingArgs: {},
      createdAt: Date.now(),
      updatedAt: Date.now(),
      assistantMessagePersisted: false,
      approvalPrompted: false,
      approvalToastShown: false,
      completionToastShown: false,
    };
  }

  function addTask(task) {
    state.taskPool[task.taskId] = task;
    return task;
  }

  function createLocalTask(sessionId, turnId) {
    return addTask(createTaskState(sessionId, generateLocalTaskId(), turnId));
  }

  function renameTask(task, newTaskId) {
    if (!task || !newTaskId || task.taskId === newTaskId) return task;
    delete state.taskPool[task.taskId];
    task.taskId = newTaskId;
    task.localOnly = false;
    state.taskPool[newTaskId] = task;
    return task;
  }

  function removeTask(taskOrId) {
    const taskId = typeof taskOrId === "string" ? taskOrId : taskOrId && taskOrId.taskId;
    if (!taskId) return;
    delete state.taskPool[taskId];
  }

  function listTasksForSession(sessionId) {
    return Object.values(state.taskPool)
      .filter(task => task && task.sessionId === sessionId)
      .sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
  }

  function listActiveTasksForSession(sessionId) {
    return listTasksForSession(sessionId).filter(task => isActiveTaskStatus(task.status));
  }

  function getLatestTaskForSession(sessionId) {
    const tasks = listTasksForSession(sessionId);
    return tasks.length ? tasks[tasks.length - 1] : null;
  }

  function findAdoptableLocalTask(sessionId, turnId) {
    const activeLocalTasks = listTasksForSession(sessionId).filter(task => task.localOnly && isActiveTaskStatus(task.status));
    if (!activeLocalTasks.length) return null;
    if (turnId) {
      return activeLocalTasks.find(task => task.turnId === turnId) || null;
    }
    return activeLocalTasks[0];
  }
  function applyServerTaskData(task, taskData) {
    task.sessionId = taskData.session_id || task.sessionId;
    task.turnId = taskData.turn_id || task.turnId || "";
    task.status = taskData.status || task.status;
    task.toolName = taskData.tool_name || task.toolName || "";
    task.toolMessage = taskData.tool_message || task.toolMessage || "";
    task.riskDescription = taskData.risk_description || task.riskDescription || "";
    task.pendingArgs = taskData.pending_args || task.pendingArgs || {};
    task.error = taskData.error || "";
    task.assistantMessagePersisted = !!taskData.assistant_message_persisted;
    task.createdAt = taskData.created_at ? taskData.created_at * 1000 : task.createdAt;
    task.updatedAt = taskData.updated_at ? taskData.updated_at * 1000 : Date.now();
    task.text = taskData.final_text || taskData.partial_text || task.text || task.toolMessage || "";
    task.completed = isTerminalTaskStatus(task.status);
    task.firstChunkReceived = task.firstChunkReceived || !!task.text || task.status === "tool_call" || task.status === "requires_approval";
    return task;
  }

  function mergeTaskFromServer(taskData) {
    if (!taskData || !taskData.task_id) return null;
    let task = state.taskPool[taskData.task_id];
    if (!task) {
      const adoptable = findAdoptableLocalTask(taskData.session_id, taskData.turn_id);
      if (adoptable) {
        task = renameTask(adoptable, taskData.task_id);
      }
    }
    if (!task) {
      task = createTaskState(taskData.session_id || state.sessionId, taskData.task_id, taskData.turn_id);
      task.localOnly = false;
      addTask(task);
    }
    return applyServerTaskData(task, taskData);
  }

  function syncTaskFromEvent(task, parsed) {
    if (!task || !parsed) return task;
    if (parsed.task_id) renameTask(task, parsed.task_id);
    if (parsed.session_id) task.sessionId = parsed.session_id;
    if (parsed.turn_id) task.turnId = parsed.turn_id;
    task.updatedAt = Date.now();
    return task;
  }

  function syncComposerState() {
    if (!sendBtn) return;
    const hasText = !!(chatInput && chatInput.value.trim());
    // 只有當前 session 有活躍任務時才禁用發送按鈕
    // 其他 session 的背景任務不影響當前 session 的輸入
    const hasActiveTaskInCurrentSession = listActiveTasksForSession(state.sessionId).length > 0;
    sendBtn.disabled = !hasText || hasActiveTaskInCurrentSession;
  }

  function getTypingIndicatorId(sessionId) {
    return "typingIndicator-" + String(sessionId || "default").replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  function renderConversationList() {
    const convList = document.getElementById("convList");
    if (!convList) return;

    const visibleSessions = state.sessions.slice();
    if (isDraftSessionId(state.sessionId) && !visibleSessions.find((session) => session.id === state.sessionId)) {
      visibleSessions.push({
        id: state.sessionId,
        title: "新對話",
        preview: "開始新的對話...",
        timestamp: Date.now(),
        _draft: true,
      });
    }

    const sorted = visibleSessions.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    let html = "";
    let currentGroup = "";

    sorted.forEach((session) => {
      const group = getDateGroupLabel(session.timestamp);
      if (group !== currentGroup) {
        currentGroup = group;
        html += '<div class="page-chat-section-label">' + escapeHtml(group) + '</div>';
      }

      const isActive = session.id === state.sessionId ? "is-active" : "";
      const displayTime = getRelativeTimeString(session.timestamp);
      const previewText = session._draft
        ? (state.sessionInputDrafts[session.id] || session.preview || "開始新的對話...")
        : session.preview;
      const activeTasks = listActiveTasksForSession(session.id);
      const latestTask = getLatestTaskForSession(session.id);
      let pendingBadge = "";
      if (activeTasks.some(task => task.status === "requires_approval")) {
        pendingBadge = ' <span style="color:#f59e0b;font-size:.75rem" title="Waiting for approval">授權中</span>';
      } else if (activeTasks.length > 0) {
        pendingBadge = ' <span style="color:var(--accent-blue);font-size:.75rem" title="Background task running">處理中</span>';
      } else if (latestTask && latestTask.status === "completed" && !latestTask.assistantMessagePersisted) {
        pendingBadge = ' <span style="color:var(--accent-green,#22c55e);font-size:.75rem" title="Task finished">已完成</span>';
      }

      html += `
        <div class="page-chat-conv-item ${isActive}" onclick="loadConversationById('${session.id}')" role="button" tabindex="0">
          <div class="page-chat-conv-icon page-chat-conv-icon--blue" aria-hidden="true">AI</div>
          <div class="page-chat-conv-info">
            <div class="page-chat-conv-name">${escapeHtml(session.title)}${pendingBadge}</div>
            <div class="page-chat-conv-preview">${escapeHtml(previewText)}</div>
          </div>
          <div class="page-chat-conv-time">${escapeHtml(displayTime)}</div>
        </div>`;
    });

    convList.innerHTML = html;
  }

  function updateSessionPreview(sessionId, text) {
    const session = state.sessions.find(item => item.id === sessionId);
    if (!session) return;
    session.preview = text.slice(0, 30) + (text.length > 30 ? "..." : "");
    if (session.title === "新對話") {
      session.title = text.slice(0, 12) + (text.length > 12 ? "..." : "");
    }
    session.timestamp = Date.now();
    saveSessions();
    renderConversationList();
  }

  async function summarizeConversationTitle(sessionId, userInput, aiResponse) {
    const session = state.sessions.find(item => item.id === sessionId);
    if (!session) return;
    const isGeneric = session.title === "新對話" || session.title.includes("...");
    if (!isGeneric) return;

    try {
      const model = getCurrentModel();
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_input: "請根據這段問答內容，產生一個 10 字以內的對話標題，只回傳標題文字。\n使用者：" + userInput + "\n助手：" + aiResponse,
          session_id: "temp-title-" + Date.now(),
          provider: model.provider || "openai",
          model: model.model || "gpt-4o",
          language: "繁體中文",
          detail_level: "簡潔",
        }),
      });
      if (!res.ok || !res.body) return;

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let summary = "";

      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const events = buffer.split("\r\n\r\n");
        buffer = events.pop() || "";
        events.forEach((event) => {
          event.split(/\r?\n/).forEach((line) => {
            if (!line.startsWith("data: ")) return;
            try {
              const payload = JSON.parse(line.slice(6));
              if (payload.status === "streaming") summary += payload.content || "";
              if (payload.status === "success") summary = payload.content || summary;
            } catch (_err) {
              // ignore
            }
          });
        });
      }

      const cleanTitle = summary.replace(/['".!?]/g, "").trim().slice(0, 10);
      if (!cleanTitle) return;
      session.title = cleanTitle;
      saveSessions();
      renderConversationList();
      if (chatTitleText && state.sessionId === sessionId) chatTitleText.textContent = cleanTitle;
    } catch (_err) {
      // silent fail
    }
  }

  function appendMeetingText(sessionId, role, text) {
    if (!text) return;
    const speaker = role === "user" ? "User" : "Assistant";
    const prev = state.sessionMeetingText[sessionId] || "";
    const next = prev + (prev ? "\n\n" : "") + speaker + ":\n" + text;
    state.sessionMeetingText[sessionId] = next;
    if (sessionId === state.sessionId) state.meetingText = next;
  }

  function renderMessage(sessionId, role, text, timestamp) {
    const container = getSessionContainer(sessionId);
    if (!container) return null;

    // 移除該 session 自己的 welcome 畫面(不影響其他 session)
    const welcome = container.querySelector(".page-chat-welcome");
    if (welcome) welcome.remove();

    const row = document.createElement("div");
    row.className = "page-chat-msg-row " + (role === "user" ? "page-chat-msg-row--user" : "page-chat-msg-row--ai");

    const dateObj = timestamp ? new Date(timestamp) : new Date();
    const hours = String(dateObj.getHours()).padStart(2, "0");
    const minutes = String(dateObj.getMinutes()).padStart(2, "0");
    const timeStr = hours + ":" + minutes;
    const safeName = userData && typeof userData.name === "string" && userData.name.trim() ? userData.name.trim() : "Workspace User";
    const safeInitials = userData && typeof userData.initials === "string" && userData.initials.trim() ? userData.initials.trim() : safeName.charAt(0);
    const initials = role === "user" ? safeInitials || "U" : "AI";
    const bubbleId = "bubble-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);

    row.innerHTML =
      '<div class="avatar avatar-sm ' +
      (role === "ai" ? "avatar-ai" : "") +
      '">' +
      escapeHtml(initials) +
      '</div>' +
      '<div class="page-chat-msg-body">' +
      '<div class="page-chat-msg-bubble" id="' + bubbleId + '">' + formatText(text) + '</div>' +
      '<div class="page-chat-msg-meta">' +
      (role === "ai" ? escapeHtml(getCurrentModelLabel()) + " · " : "") +
      escapeHtml(timeStr) +
      '<div class="page-chat-msg-actions">' +
      '<button class="page-chat-msg-action-btn" onclick="copyMsg(this)" title="Copy">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
      '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>' +
      '</svg></button></div></div></div>';

    container.appendChild(row);
    scrollSessionToBottom(sessionId);
    return container.querySelector("#" + bubbleId);
  }

  function showTyping(sessionId) {
    const container = getSessionContainer(sessionId);
    if (!container) return;
    removeTyping(sessionId);
    const row = document.createElement("div");
    row.className = "page-chat-typing-row";
    row.id = getTypingIndicatorId(sessionId);
    row.innerHTML =
      '<div class="avatar avatar-sm avatar-ai">AI</div>' +
      '<div class="page-chat-typing-bubble">' +
      '<div class="page-chat-typing-dot"></div>' +
      '<div class="page-chat-typing-dot"></div>' +
      '<div class="page-chat-typing-dot"></div>' +
      '</div>';
    container.appendChild(row);
    scrollSessionToBottom(sessionId);
  }

  function removeTyping(sessionId) {
    const el = document.getElementById(getTypingIndicatorId(sessionId));
    if (el) el.remove();
  }

  function showTaskBubble(task, isFinal) {
    // Session-aware: 永遠寫入該 task 自己的 session 容器,不管當前是否可見
    if (!task || !task.sessionId) return;
    const container = getSessionContainer(task.sessionId);
    if (!container) return;

    let bubble = task.bubbleEl;
    if (!bubble || !container.contains(bubble)) {
      bubble = renderMessage(task.sessionId, "ai", task.text || "");
      task.bubbleEl = bubble;
    }
    if (!bubble) return;
    const row = bubble.closest(".page-chat-msg-row");
    if (row) row.style.display = "";
    bubble.innerHTML = formatText(task.text || "") + (isFinal ? "" : '<span class="page-chat-cursor"></span>');
    scrollSessionToBottom(task.sessionId);
  }

  function autoResize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }
  function restoreSessionTaskUI(sessionId) {
    // 可以針對任何 session 重建,不限於當前 session,因為每個 session 有自己的容器
    const tasks = listTasksForSession(sessionId);
    if (!tasks.length) return;

    tasks.forEach((task) => {
      task.bubbleEl = null;
    });

    const needsTyping = tasks.some(task => isActiveTaskStatus(task.status) && !task.firstChunkReceived && !task.error);
    if (needsTyping) {
      showTyping(sessionId);
    } else {
      removeTyping(sessionId);
    }

    tasks.forEach((task) => {
      if (task.assistantMessagePersisted && task.status === "completed") return;
      if (!task.text && task.status !== "tool_call" && task.status !== "requires_approval") return;
      showTaskBubble(task, task.completed);
    });

    // 只有當前可見 session 才彈出 approval modal
    if (sessionId === state.sessionId) {
      maybePromptApprovalForCurrentSession(sessionId);
    }
  }

  function maybePromptApprovalForCurrentSession(sessionId) {
    if (sessionId !== state.sessionId) return;
    const task = listTasksForSession(sessionId).find(item => item.status === "requires_approval" && !item.approvalPrompted);
    if (task) {
      handleApproval(task);
    }
  }

  function resetSession() {
    const newId = generateDraftSessionId();
    state.sessionId = newId;
    localStorage.setItem("kway_chat_session", newId);
    state.startAt = Date.now();
    state.sessionMsgCounts[newId] = 0;
    state.sessionTokenCounts[newId] = 0;
    state.sessionMeetingText[newId] = "";
    state.msgCount = 0;
    state.tokenCount = 0;
    state.meetingText = "";
    // 建立新 session 的 container 並隱藏其他 session
    getSessionContainer(newId);
    showSessionContainer(newId);
    renderConversationList();
    updateStats();
    syncComposerState();
  }

  async function loadModels() {
    try {
      const res = await fetch("/api/models");
      if (res.ok) {
        const data = await res.json();
        if (data && data.status === "success" && Array.isArray(data.models) && data.models.length > 0) {
          state.models = data.models;
        }
      }
    } catch (_err) {
      // keep defaults
    }

    if (modelName) modelName.textContent = getCurrentModelLabel();

    const raw = localStorage.getItem("kway_settings");
    if (!raw) return;
    try {
      const settings = JSON.parse(raw);
      if (!settings.model) return;
      const idx = state.models.findIndex(item => item.model === settings.model);
      if (idx !== -1) {
        state.modelIndex = idx;
        if (modelName) modelName.textContent = getCurrentModelLabel();
      }
    } catch (_err) {
      // ignore malformed settings
    }
  }

  async function loadSideInfo() {
    try {
      const [skillsRes, docsRes] = await Promise.all([fetch("/skills/list"), fetch("/api/documents/list")]);

      const toolsTab = document.querySelector("#tab-tools .page-chat-info-section");
      if (toolsTab && skillsRes.ok) {
        const data = await skillsRes.json();
        const entries = Object.entries(data.skills || {}).slice(0, 6);
        let html = '<div class="page-chat-info-section-title">Loaded MCP Skills</div>';
        if (entries.length === 0) {
          html += '<div class="page-chat-info-card"><div class="page-chat-info-card-desc">No skills loaded</div></div>';
        } else {
          entries.forEach((entry) => {
            const meta = entry[1] || {};
            html +=
              '<div class="page-chat-info-card">' +
              '<div class="page-chat-info-card-title">' + escapeHtml(entry[0]) + '</div>' +
              '<div class="page-chat-info-card-desc">' + escapeHtml(meta.description || "No description") + '</div></div>';
          });
        }
        toolsTab.innerHTML = html;
      }

      if (docsRes.ok) {
        const docs = await docsRes.json();
        const infoCards = document.querySelectorAll("#tab-info .page-chat-info-card");
        if (infoCards[0]) {
          infoCards[0].insertAdjacentHTML(
            "beforeend",
            '<div class="page-chat-stat-row"><span class="page-chat-stat-row-label">Indexed docs</span><span class="page-chat-stat-row-value">' +
              String(docs.total || 0) +
              '</span></div>'
          );
        }
      }
    } catch (_err) {
      // non-blocking enhancement
    }
  }

  async function loadHistory(targetSessionId, viewToken) {
    const sid = targetSessionId || state.sessionId;
    try {
      const res = await fetch("/chat/session/" + encodeURIComponent(sid));
      if (!isSessionLoadCurrent(sid, viewToken)) return { hasHistory: false, aborted: true, history: [] };
      if (!res.ok) return { hasHistory: false, aborted: false, history: [] };

      const data = await res.json();
      if (!isSessionLoadCurrent(sid, viewToken)) return { hasHistory: false, aborted: true, history: [] };
      const history = Array.isArray(data.history) ? data.history : [];
      return { hasHistory: history.length > 0, aborted: false, history: history };
    } catch (_err) {
      return { hasHistory: false, aborted: false, history: [] };
    }
  }

  async function loadSessionTasks(targetSessionId, viewToken) {
    const sid = targetSessionId || state.sessionId;
    try {
      const res = await fetch("/chat/tasks/session/" + encodeURIComponent(sid));
      if (!isSessionLoadCurrent(sid, viewToken)) return { tasks: [], aborted: true };
      if (!res.ok) return { tasks: [], aborted: false };

      const data = await res.json();
      if (!isSessionLoadCurrent(sid, viewToken)) return { tasks: [], aborted: true };
      const tasks = Array.isArray(data.tasks) ? data.tasks : [];
      return { tasks: tasks.map(mergeTaskFromServer).filter(Boolean), aborted: false };
    } catch (_err) {
      return { tasks: [], aborted: false };
    }
  }

  function reconcileSessionTasksAfterLoad(sessionId, hasHistory) {
    listTasksForSession(sessionId).forEach((task) => {
      if (task.assistantMessagePersisted && task.status === "completed" && hasHistory) {
        removeTask(task);
      }
    });
  }

  function renderLoadedSession(sessionId, history) {
    // 可針對任何 session 渲染,因為每個 session 有自己的容器
    const normalizedHistory = Array.isArray(history) ? history.map(cloneHistoryMessage).filter(Boolean) : [];

    if (normalizedHistory.length > 0) {
      renderHistoryMessages(sessionId, mergeHistoryWithCache(sessionId, normalizedHistory));
      return;
    }

    const cachedHistory = getCachedHistory(sessionId);
    if (cachedHistory.length > 0) {
      renderHistoryMessages(sessionId, cachedHistory);
      return;
    }

    if (listTasksForSession(sessionId).length > 0) {
      clearSessionContainer(sessionId);
      return;
    }

    renderConversationPlaceholder(sessionId, "這個對話目前是空的", "輸入訊息開始新的任務，或切換到其他對話。");
  }

  async function handleApproval(task) {
    if (!task) return "";
    if (task.sessionId !== state.sessionId) {
      if (!task.approvalToastShown) {
        task.approvalToastShown = true;
        showToast("「" + getSessionTitle(task.sessionId) + "」正在等待授權", "info");
        renderConversationList();
      }
      return task.text;
    }

    task.approvalPrompted = true;
    dismissApprovalModal(false);

    return new Promise((resolve) => {
      const modal = document.createElement("div");
      modal.id = "authApprovalModal";
      modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:9999;";
      modal.innerHTML =
        '<div style="background:var(--bg-surface,#1e1e2e);border-radius:12px;max-width:460px;width:92%;box-shadow:0 8px 32px rgba(0,0,0,.4);">' +
        '<div style="background:linear-gradient(135deg,#f05252,#d03030);border-radius:12px 12px 0 0;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;">' +
        '<div><p style="color:#fff;margin:0;font-weight:600;font-size:1rem;">高風險操作需要授權</p>' +
        '<p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:.82rem;">這筆授權屬於對話：' + escapeHtml(getSessionTitle(task.sessionId)) + '</p></div>' +
        '<button id="authModalCloseBtn" style="background:none;border:none;color:#fff;font-size:1.1rem;cursor:pointer;padding:4px;">×</button></div>' +
        '<div style="padding:20px;">' +
        '<p style="margin:0 0 6px;font-weight:600;font-size:.85rem;color:var(--text-secondary,#aaa);">技能</p>' +
        '<p style="margin:0 0 14px;font-family:monospace;background:var(--bg-base,#13131f);padding:8px 12px;border-radius:8px;color:#60a5fa;font-size:.9rem;">' + escapeHtml(task.toolName || "Unknown tool") + '</p>' +
        '<p style="margin:0 0 6px;font-weight:600;font-size:.85rem;color:var(--text-secondary,#aaa);">風險說明</p>' +
        '<p style="margin:0;color:var(--text-secondary,#aaa);font-size:.88rem;line-height:1.55;">' + escapeHtml(task.riskDescription || "High-risk operation") + '</p></div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end;padding:14px 20px;border-top:1px solid var(--border-subtle,rgba(255,255,255,.08));">' +
        '<button id="authRejectBtn" style="padding:8px 20px;border-radius:8px;border:1px solid var(--border-subtle,rgba(255,255,255,.15));background:none;color:var(--text-primary,#e0e0e0);cursor:pointer;font-size:.9rem;">拒絕</button>' +
        '<button id="authApproveBtn" style="padding:8px 20px;border-radius:8px;border:none;background:#f05252;color:#fff;cursor:pointer;font-size:.9rem;font-weight:600;">授權並繼續</button></div></div>';
      document.body.appendChild(modal);
      state.activeApprovalTaskId = task.taskId;

      function closeModal() {
        if (state.activeApprovalTaskId === task.taskId) {
          state.activeApprovalTaskId = null;
        }
        modal.remove();
      }

      async function doReject() {
        closeModal();
        try {
          await fetch("/chat/tasks/" + encodeURIComponent(task.taskId) + "/reject", { method: "POST" });
        } catch (_err) {
          // best effort
        }
        task.status = "rejected";
        task.completed = true;
        task.error = "Rejected by user";
        task.text = (task.text ? task.text + "\n\n" : "") + "已取消「" + (task.toolName || "tool") + "」的授權。";
        showTaskBubble(task, true);
        renderConversationList();
        syncComposerState();
        resolve(task.text);
      }

      async function doApprove() {
        closeModal();
        task.status = "approved";
        task.completed = false;
        task.error = "";
        task.text = "";
        task.firstChunkReceived = false;
        showTyping(task.sessionId);

        try {
          const res = await fetch("/chat/tasks/" + encodeURIComponent(task.taskId) + "/approve", { method: "POST" });
          if (!res.ok) {
            const errText = await res.text();
            task.status = "error";
            task.completed = true;
            task.error = errText;
            task.text = "授權後執行失敗：\n\n" + errText;
            removeTyping(task.sessionId);
            showTaskBubble(task, true);
            renderConversationList();
            syncComposerState();
            resolve(task.text);
            return;
          }

          const finalText = await streamChatResponse(res, task);
          resolve(finalText);
        } catch (err) {
          task.status = "error";
          task.completed = true;
          task.error = err.message || "";
          task.text = "授權後執行失敗：\n\n" + (err.message || "");
          removeTyping(task.sessionId);
          showTaskBubble(task, true);
          renderConversationList();
          syncComposerState();
          resolve(task.text);
        }
      }

      modal.querySelector("#authModalCloseBtn").onclick = doReject;
      modal.querySelector("#authRejectBtn").onclick = doReject;
      modal.querySelector("#authApproveBtn").onclick = doApprove;
    });
  }
  async function streamChatResponse(res, task) {
    if (!res.body) return "";
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const events = buffer.split("\r\n\r\n");
      buffer = events.pop() || "";

      for (const event of events) {
        const lines = event.split(/\r?\n/);
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (!payload || payload === "[DONE]") continue;

          let parsed = null;
          try {
            parsed = JSON.parse(payload);
          } catch (_err) {
            continue;
          }

          syncTaskFromEvent(task, parsed);

          if (parsed.status === "task_started" || parsed.status === "task_resumed") {
            task.status = parsed.status === "task_resumed" ? "approved" : task.status;
            continue;
          }
          if (parsed.status === "provider_meta") {
            continue;
          }
          if (parsed.status === "streaming") {
            task.status = "running";
            task.firstChunkReceived = true;
            task.text += parsed.content || "";
            removeTyping(task.sessionId);
            showTaskBubble(task, false);
            continue;
          }
          if (parsed.status === "tool_call") {
            task.status = "tool_call";
            task.toolName = parsed.tool_name || task.toolName || "";
            task.toolMessage = parsed.message || "";
            task.text = parsed.message || ("正在執行技能：" + (task.toolName || "..."));
            task.firstChunkReceived = true;
            removeTyping(task.sessionId);
            showTaskBubble(task, false);
            continue;
          }
          if (parsed.status === "requires_approval") {
            task.status = "requires_approval";
            task.completed = false;
            task.toolName = parsed.tool_name || task.toolName || "";
            task.riskDescription = parsed.risk_description || task.riskDescription || "";
            task.pendingArgs = parsed.pending_args || task.pendingArgs || {};
            task.firstChunkReceived = true;
            removeTyping(task.sessionId);
            try {
              reader.cancel();
            } catch (_err) {
              // ignore
            }
            if (task.sessionId === state.sessionId) {
              return await handleApproval(task);
            }
            if (!task.approvalToastShown) {
              task.approvalToastShown = true;
              showToast("「" + getSessionTitle(task.sessionId) + "」正在等待授權", "info");
              renderConversationList();
            }
            syncComposerState();
            return task.text;
          }
          if (parsed.status === "success") {
            task.status = "completed";
            task.completed = true;
            task.assistantMessagePersisted = true;
            task.text = parsed.content || task.text;
            removeTyping(task.sessionId);
            showTaskBubble(task, true);
            return task.text;
          }
          if (parsed.status === "error") {
            task.status = "error";
            task.completed = true;
            task.error = parsed.message || "Server error";
            throw new Error(task.error);
          }
        }
      }
    }

    removeTyping(task.sessionId);
    if (task.text) {
      showTaskBubble(task, !!task.completed);
    }
    return task.text;
  }

  function onTaskComplete(task, content, finalText) {
    if (!task) return;
    renderConversationList();
    if (!task.completed) {
      syncComposerState();
      return;
    }

    if (task.status === "completed") {
      const taskSession = task.sessionId;
      // 無論當前可見哪個 session,都要把訊息寫入該 task 自己的 session 統計
      state.sessionMsgCounts[taskSession] = (state.sessionMsgCounts[taskSession] || 0) + 1;
      const tokenDelta = Math.ceil((finalText || "").length / 4);
      state.sessionTokenCounts[taskSession] = (state.sessionTokenCounts[taskSession] || 0) + tokenDelta;
      appendMeetingText(taskSession, "user", content);
      appendMeetingText(taskSession, "assistant", finalText || "");
      appendCachedHistoryMessage(taskSession, "assistant", finalText || "");

      if (taskSession === state.sessionId) {
        state.msgCount = state.sessionMsgCounts[taskSession];
        state.tokenCount = state.sessionTokenCounts[taskSession];
        updateStats();
        if (state.msgCount <= 2 && finalText) {
          summarizeConversationTitle(taskSession, content, finalText);
        }
      } else {
        if (state.sessionMsgCounts[taskSession] <= 2 && finalText) {
          summarizeConversationTitle(taskSession, content, finalText);
        }
        if (!task.completionToastShown) {
          task.completionToastShown = true;
          showToast("「" + getSessionTitle(taskSession) + "」的任務已完成", "success");
        }
      }
      removeTask(task);
    } else if (task.status === "rejected" && task.sessionId === state.sessionId) {
      showTaskBubble(task, true);
    }

    renderConversationList();
    syncComposerState();
  }

  function onTaskError(task, err) {
    if (!task) return;
    removeTyping(task.sessionId);
    task.status = "error";
    task.completed = true;
    task.firstChunkReceived = true;
    task.error = err && err.message ? err.message : "Request failed";
    task.text = "任務執行失敗：\n\n" + task.error;
    task.bubbleEl = null;

    // 錯誤訊息永遠渲染到該 task 自己的 session container
    renderMessage(task.sessionId, "ai", task.text);

    if (task.sessionId !== state.sessionId) {
      showToast("「" + getSessionTitle(task.sessionId) + "」的任務執行失敗", "error");
    } else {
      showToast("Chat request failed", "error");
    }
    removeTask(task);
    renderConversationList();
    syncComposerState();
  }

  async function runTaskInBackground(res, task, content) {
    try {
      const finalText = await streamChatResponse(res, task);
      onTaskComplete(task, content, finalText);
    } catch (err) {
      onTaskError(task, err);
    }
  }

  async function sendMessage(text) {
    const content = (text || "").trim();
    let requestSessionId = state.sessionId;
    // 只檢查當前 session 自己是否還在 pending;其他 session 的 task 完全不影響
    if (!content || listActiveTasksForSession(requestSessionId).length > 0) return;

    if (isDraftSessionId(requestSessionId)) {
      requestSessionId = materializeDraftSession(requestSessionId, content);
    }
    const turnId = generateTurnId();

    if (chatInput) {
      chatInput.value = "";
      // 同步清除該 session 的草稿
      state.sessionInputDrafts[requestSessionId] = "";
      autoResize(chatInput);
    }
    syncComposerState();

    ensureSessionExists(requestSessionId, "新對話", "開始新的對話...");
    // 渲染到該 session 自己的 container(即使使用者切到其他 session,這筆訊息仍然留在原 session)
    renderMessage(requestSessionId, "user", content);
    appendCachedHistoryMessage(requestSessionId, "user", content);

    // Per-session 統計累加
    state.sessionMsgCounts[requestSessionId] = (state.sessionMsgCounts[requestSessionId] || 0) + 1;
    const tokenDelta = Math.ceil(content.length / 4);
    state.sessionTokenCounts[requestSessionId] = (state.sessionTokenCounts[requestSessionId] || 0) + tokenDelta;
    if (requestSessionId === state.sessionId) {
      state.msgCount = state.sessionMsgCounts[requestSessionId];
      state.tokenCount = state.sessionTokenCounts[requestSessionId];
      updateStats();
    }

    const task = createLocalTask(requestSessionId, turnId);
    showTyping(requestSessionId);
    updateSessionPreview(requestSessionId, content);

    try {
      const model = getCurrentModel();
      const rawSettings = localStorage.getItem("kway_settings");
      let language = "繁體中文";
      let detailLevel = "詳細";
      if (rawSettings) {
        try {
          const settings = JSON.parse(rawSettings);
          language = settings.language || language;
          detailLevel = settings.detail || detailLevel;
        } catch (_err) {
          // ignore malformed settings
        }
      }

      const payload = {
        user_input: content,
        session_id: requestSessionId,
        turn_id: turnId,
        provider: model.provider || "openai",
        model: model.model || "gpt-4o",
        language: language,
        detail_level: detailLevel,
      };

      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        removeTyping(requestSessionId);
        const errText = await res.text();
        throw new Error("HTTP " + res.status + ": " + errText);
      }

      renderConversationList();
      runTaskInBackground(res, task, content);
    } catch (err) {
      onTaskError(task, err);
    }
  }

  window.sendSuggestion = function (text) {
    if (chatInput) chatInput.value = text;
    sendMessage(text);
  };

  window.switchTab = function (btn, name) {
    document.querySelectorAll(".page-chat-tab-btn").forEach(function (item) {
      item.classList.remove("is-active");
    });
    btn.classList.add("is-active");
    ["info", "tools", "history"].forEach(function (tab) {
      const el = document.getElementById("tab-" + tab);
      if (el) el.style.display = tab === name ? "block" : "none";
    });
  };

  window.cycleModel = function () {
    state.modelIndex = (state.modelIndex + 1) % state.models.length;
    if (modelName) modelName.textContent = getCurrentModelLabel();
    showToast("Model switched to " + getCurrentModelLabel(), "success");
  };

  window.copyMsg = function (btn) {
    const bubble = btn.closest(".page-chat-msg-body")?.querySelector(".page-chat-msg-bubble");
    if (!bubble) return;
    navigator.clipboard.writeText(bubble.innerText).then(function () {
      showToast("Copied", "success");
    });
  };

  window.downloadMeetingMd = function () {
    if (!state.meetingText.trim()) {
      showToast("No conversation to export", "info");
      return;
    }
    const blob = new Blob([state.meetingText.trim()], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mcp-meeting-notes-" + new Date().toISOString().slice(0, 10) + ".md";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    showToast("Markdown exported", "success");
  };
  window.newConversation = function () {
    const previousSessionId = state.sessionId;
    // 切換前保存當前 session 的草稿
    if (chatInput && state.sessionId) {
      state.sessionInputDrafts[state.sessionId] = chatInput.value || "";
    }
    if (isDraftSessionId(previousSessionId) && !state.sessions.find((item) => item.id === previousSessionId)) {
      listTasksForSession(previousSessionId).forEach(removeTask);
      removeSessionContainer(previousSessionId);
      delete state.sessionHistoryCache[previousSessionId];
    }
    resetSession();
    renderConversationPlaceholder(state.sessionId, "開始新的對話", "輸入任何問題，或上傳音檔讓助手協助處理。");
    if (chatTitleText) chatTitleText.textContent = "新對話";
    if (chatInput) {
      chatInput.value = "";
      autoResize(chatInput);
    }
    showToast("已建立新的對話", "success");
  };

  window.clearConversation = window.newConversation;

  window.confirmDeleteCurrentConversation = function () {
    const modal = document.getElementById("deleteModal");
    const confirmBtn = document.getElementById("confirmDeleteBtn");
    if (!modal || !confirmBtn) return;
    modal.style.display = "flex";
    confirmBtn.onclick = function () {
      deleteCurrentConversation();
      modal.style.display = "none";
    };
  };

  window.closeDeleteModal = function () {
    const modal = document.getElementById("deleteModal");
    if (modal) modal.style.display = "none";
  };

  function deleteCurrentConversation() {
    const deletingId = state.sessionId;
    const idx = state.sessions.findIndex(item => item.id === deletingId);
    if (idx !== -1) {
      state.sessions.splice(idx, 1);
      saveSessions();
      listTasksForSession(deletingId).forEach(removeTask);
      // 徹底移除該 session 的 container 與所有 per-session 狀態
      removeSessionContainer(deletingId);
      delete state.sessionHistoryCache[deletingId];

      if (state.sessions.length > 0) {
        const nextSessionId = state.sessions[state.sessions.length - 1].id;
        window.loadConversationById(nextSessionId, true);
      } else {
        window.newConversation();
      }
    } else {
      if (isDraftSessionId(deletingId)) {
        listTasksForSession(deletingId).forEach(removeTask);
        removeSessionContainer(deletingId);
        delete state.sessionHistoryCache[deletingId];
      }
      window.newConversation();
    }
  }

  window.loadConversationById = async function (sid, forceReload) {
    if (!sid) return;
    const prevSessionId = state.sessionId;
    const isSameSession = sid === prevSessionId;

    // 切換前先保存當前 session 的 composer 草稿
    if (!isSameSession && chatInput) {
      state.sessionInputDrafts[prevSessionId] = chatInput.value || "";
    }

    // 關掉上一個 session 的 approval modal (如果還開著)
    if (!isSameSession) {
      dismissApprovalModal(true);
    }

    // 1. 切換 session 狀態
    state.sessionId = sid;
    localStorage.setItem("kway_chat_session", state.sessionId);

    const session = state.sessions.find(item => item.id === sid);
    if (chatTitleText) chatTitleText.textContent = session ? session.title : getSessionTitle(sid);

    // 2. 顯示/隱藏各 session 容器 — 不清空任何既有 DOM
    showSessionContainer(sid);

    // 3. 還原該 session 的統計、草稿、composer 狀態
    applySessionStats(sid);
    if (chatInput) {
      chatInput.value = state.sessionInputDrafts[sid] || "";
      autoResize(chatInput);
    }

    renderConversationList();
    syncComposerState();

    if (isDraftSessionId(sid)) {
      const cachedHistory = getCachedHistory(sid);
      if (cachedHistory.length > 0) {
        renderHistoryMessages(sid, cachedHistory);
        restoreSessionTaskUI(sid);
      } else if (listTasksForSession(sid).length > 0) {
        restoreSessionTaskUI(sid);
      } else {
        renderConversationPlaceholder(sid, "開始新的對話", "輸入任何問題，或上傳音檔讓助手協助處理。");
      }
      state.sessionHistoryLoaded[sid] = true;
      return;
    }

    // 4. 如果該 session 的 container 是空的 (第一次進來),顯示 placeholder 再背景載入
    const container = getSessionContainer(sid);
    const containerIsEmpty = container && container.children.length === 0;
    if (containerIsEmpty) {
      const cachedHistory = getCachedHistory(sid);
      if (cachedHistory.length > 0) {
        renderHistoryMessages(sid, cachedHistory);
        restoreSessionTaskUI(sid);
      } else if (listTasksForSession(sid).length > 0) {
        // 有 task 但沒有歷史 — 不清空,讓 task 的 bubble 直接繪上去
        restoreSessionTaskUI(sid);
      } else {
        renderConversationPlaceholder(sid, "正在載入對話", "正在同步這個對話的歷史訊息...");
      }
    } else if (!forceReload && state.sessionHistoryLoaded[sid]) {
      // 已載入過,直接捲到底就好
      scrollSessionToBottom(sid);
      return;
    }

    // 5. 背景載入歷史和任務狀態 — 不會清空 container,只在必要時重繪
    const viewToken = nextSessionLoadToken(sid);
    const historyPromise = loadHistory(sid, viewToken);
    const taskPromise = loadSessionTasks(sid, viewToken);

    const historyResult = await historyPromise;
    // 即便使用者已經切到別的 session,我們還是要把歷史渲染到「對應 session 的 container」
    // 所以不再 early return — 只是不更新全域統計
    if (historyResult.aborted) return;

    if (historyResult.hasHistory) {
      renderLoadedSession(sid, historyResult.history);
      restoreSessionTaskUI(sid);
      state.sessionHistoryLoaded[sid] = true;
    } else if (containerIsEmpty && getCachedHistory(sid).length === 0 && listTasksForSession(sid).length === 0) {
      renderConversationPlaceholder(sid, "這個對話目前是空的", "輸入訊息開始新的任務，或切換到其他對話。");
      state.sessionHistoryLoaded[sid] = true;
    } else {
      state.sessionHistoryLoaded[sid] = true;
    }

    if (sid === state.sessionId) {
      applySessionStats(sid);
      syncComposerState();
    }

    const taskResult = await taskPromise;
    if (taskResult.aborted) return;

    if (historyResult.hasHistory || getCachedHistory(sid).length > 0 || listTasksForSession(sid).length > 0) {
      ensureSessionRecord(sid, session ? session.title : "新對話");
    }

    reconcileSessionTasksAfterLoad(sid, historyResult.hasHistory || getCachedHistory(sid).length > 0);
    restoreSessionTaskUI(sid);
    renderConversationList();
    if (sid === state.sessionId) syncComposerState();
  };

  window.loadConversation = function (idx) {
    const session = state.sessions[idx];
    if (session) window.loadConversationById(session.id, true);
  };

  window.triggerAudioUpload = function () {
    const audioFileInput = document.getElementById("audioFileInput");
    if (!audioFileInput) return;
    audioFileInput.value = "";
    audioFileInput.onchange = async function () {
      const file = audioFileInput.files[0];
      if (!file) return;

      showToast("正在上傳音檔...", "info");
      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("/workspace/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (data.status !== "success") throw new Error(data.detail || "Upload failed");

        const msg = "幫我將這個音訊檔案轉換為逐字稿，file_path: " + data.filepath;
        if (chatInput) chatInput.value = msg;
        showToast("已上傳 " + file.name, "success");
        sendMessage(msg);
      } catch (err) {
        showToast("音檔上傳失敗：" + err.message, "error");
      }
    };
    audioFileInput.click();
  };

  if (chatInput && sendBtn) {
    chatInput.addEventListener("input", function () {
      autoResize(chatInput);
      // 即時保存當前 session 的草稿,切換回來時恢復
      if (state.sessionId) {
        state.sessionInputDrafts[state.sessionId] = chatInput.value || "";
      }
      syncComposerState();
    });
    chatInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage(chatInput.value);
      }
    });
    sendBtn.addEventListener("click", function () {
      sendMessage(chatInput.value);
    });
  }

  const topbarAvatar = document.getElementById("topbarAvatar");
  const sidebarAvatar = document.getElementById("sidebarAvatar");
  const sidebarName = document.getElementById("sidebarName");
  const sidebarDept = document.getElementById("sidebarDept");
  const safeName = userData && typeof userData.name === "string" && userData.name.trim() ? userData.name.trim() : "Workspace User";
  const safeInitials = userData && typeof userData.initials === "string" && userData.initials.trim() ? userData.initials.trim() : safeName.charAt(0);

  function setAvatar(el) {
    if (!el) return;
    const pic = userData && typeof userData.picture === "string" && userData.picture.trim() ? userData.picture.trim() : "";
    const fallbackText = safeInitials || "U";

    if (pic) {
      el.innerHTML = "";
      const img = document.createElement("img");
      img.src = pic;
      img.alt = safeName;
      img.referrerPolicy = "no-referrer";
      img.style.width = "100%";
      img.style.height = "100%";
      img.style.borderRadius = "50%";
      img.style.objectFit = "cover";
      img.onerror = function () {
        el.innerHTML = "";
        el.textContent = fallbackText;
      };
      el.appendChild(img);
    } else {
      el.textContent = fallbackText;
    }
  }

  setAvatar(topbarAvatar);
  setAvatar(sidebarAvatar);
  if (sidebarName) sidebarName.textContent = safeName;
  if (sidebarDept) sidebarDept.textContent = (userData.dept || "MCP Workspace") + " · Connected";

  setInterval(updateSessionDuration, 1000);
  updateSessionDuration();
  updateStats();
  loadModels();

  hydrateAuthFromServer().finally(function () {
    loadSideInfo();
    renderConversationList();
    window.loadConversationById(state.sessionId, true);
    syncComposerState();
  });
})();
