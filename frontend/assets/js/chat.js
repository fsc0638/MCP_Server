(function () {
  "use strict";

  const state = {
    msgCount: 0,
    tokenCount: 0,
    startAt: Date.now(),
    pendingSessions: {},
    models: [{ provider: "openai", model: "gpt-4o", display_name: "OpenAI (gpt-4o)" }],
    modelIndex: 0,
    // Use LINE web-login session id if available; otherwise start a fresh web session.
    sessionId: localStorage.getItem("kway_chat_session") || ("web-" + Math.random().toString(36).slice(2, 10)),
    meetingText: "",
    sessions: JSON.parse(localStorage.getItem("kway_sessions") || "[]")
  };
  localStorage.setItem("kway_chat_session", state.sessionId);

  async function hydrateAuthFromServer() {
    // If user already exists, do nothing.
    try {
      const existing = sessionStorage.getItem("kway_user");
      if (existing) return;

      const res = await fetch("/api/auth/me", { credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.status === "success" && data.user && data.user.id) {
        sessionStorage.setItem("kway_user", JSON.stringify(data.user));
        // Align chat session bucket to LINE id.
        localStorage.setItem("kway_chat_session", data.user.id);
        state.sessionId = data.user.id;
      }
    } catch (_e) {
      // best-effort
    }
  }

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

    // Use calendar-day difference (midnight-based) for accurate day labels
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const targetStart = new Date(target.getFullYear(), target.getMonth(), target.getDate());
    const calendarDays = Math.round((todayStart - targetStart) / 86400000);

    if (calendarDays === 0) {
      // Today: show relative time
      if (minutes < 1) return "剛剛";
      if (minutes < 60) return minutes + " 分鐘前";
      return Math.floor(diff / 3600000) + " 小時前";
    }
    if (calendarDays === 1) return "昨天";
    if (calendarDays <= 7) return calendarDays + " 天前";
    // Older than a week: show date
    return (target.getMonth() + 1) + "/" + target.getDate();
  }

  // Determine which date-group label a timestamp belongs to
  function getDateGroupLabel(timestamp) {
    if (!timestamp) return "更早";
    const now = new Date();
    const target = new Date(timestamp);
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const targetStart = new Date(target.getFullYear(), target.getMonth(), target.getDate());
    const calendarDays = Math.round((todayStart - targetStart) / 86400000);

    if (calendarDays === 0) return "今日對話";
    if (calendarDays === 1) return "昨天";
    if (calendarDays <= 7) return "前 7 天";
    if (calendarDays <= 30) return "前 30 天";
    return target.getFullYear() + "/" + (target.getMonth() + 1);
  }

  function updateStats(extraTokens) {
    if (extraTokens) state.tokenCount += extraTokens;
    const statMsgCount = document.getElementById("statMsgCount");
    const statTokens = document.getElementById("statTokens");
    if (statMsgCount) statMsgCount.textContent = String(state.msgCount);
    if (statTokens) statTokens.textContent = String(state.tokenCount);
  }

  function updateSessionDuration() {
    const statDuration = document.getElementById("statDuration");
    if (!statDuration) return;
    const elapsed = Math.floor((Date.now() - state.startAt) / 1000);
    const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const ss = String(elapsed % 60).padStart(2, "0");
    statDuration.textContent = mm + ":" + ss;
  }

  function removeChatWelcome() {
    const welcome = document.getElementById("chatWelcome");
    if (welcome) welcome.remove();
  }

  function saveSessions() {
    localStorage.setItem("kway_sessions", JSON.stringify(state.sessions));
  }

  /**
   * Ensure current session exists in sessions list.
   * Called lazily — only when user actually sends a message.
   */
  function ensureSessionExists() {
    if (!state.sessions.find(s => s.id === state.sessionId)) {
      state.sessions.push({
        id: state.sessionId,
        title: "新對話",
        preview: "詢問任何問題...",
        timestamp: Date.now()
      });
      saveSessions();
    }
  }

  function renderConversationList() {
    const convList = document.getElementById("convList");
    if (!convList) return;

    // Sort sessions by timestamp descending (most recent first)
    const sorted = state.sessions.slice().sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

    let html = "";
    let currentGroup = "";
    sorted.forEach((s) => {
      const group = getDateGroupLabel(s.timestamp);
      if (group !== currentGroup) {
        currentGroup = group;
        html += '<div class="page-chat-section-label">' + escapeHtml(group) + '</div>';
      }
      const isActive = s.id === state.sessionId ? "is-active" : "";
      const displayTime = getRelativeTimeString(s.timestamp);
      html += `
        <div class="page-chat-conv-item ${isActive}" onclick="loadConversationById('${s.id}')" role="button" tabindex="0">
          <div class="page-chat-conv-icon page-chat-conv-icon--blue" aria-hidden="true">✦</div>
          <div class="page-chat-conv-info">
            <div class="page-chat-conv-name">${escapeHtml(s.title)}</div>
            <div class="page-chat-conv-preview">${escapeHtml(s.preview)}</div>
          </div>
          <div class="page-chat-conv-time">${escapeHtml(displayTime)}</div>
        </div>`;
    });
    convList.innerHTML = html;
  }

  function updateCurrentSessionPreview(text, isFirstMessage = false) {
    const session = state.sessions.find(s => s.id === state.sessionId);
    if (session) {
      session.preview = text.slice(0, 30) + (text.length > 30 ? "..." : "");
      if (session.title === "新對話") {
        session.title = text.slice(0, 12) + (text.length > 12 ? "..." : "");
      }
      session.timestamp = Date.now(); // Update timestamp for relative ordering
      saveSessions();
      renderConversationList();
    }
  }

  async function summarizeConversationTitle(userInput, aiResponse) {
    const session = state.sessions.find(s => s.id === state.sessionId);
    if (!session) return;
    
    // Only summarize if it's still generic "New Conversation" or a raw preview
    const isGeneric = session.title === "新對話" || session.title.includes("...");
    if (!isGeneric) return;

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_input: `請根據以下對話摘要一個「不超過10個字」的標題，只需回答標題內容，不要有標點符號：\n問：${userInput}\n答：${aiResponse}`,
          session_id: "temp-title-" + Date.now(),
          language: "繁體中文",
          detail_level: "簡潔"
        })
      });
      if (!res.ok) return;

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let summary = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const lines = decoder.decode(value).split("\r\n\r\n");
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const p = JSON.parse(line.slice(6));
              if (p.status === "streaming") summary += p.content;
              else if (p.status === "success") summary = p.content;
            } catch(e){}
          }
        }
      }
      
      const cleanTitle = summary.replace(/[「」『』"'\.\!\?]/g, '').trim().slice(0, 10);
      if (cleanTitle) {
        session.title = cleanTitle;
        saveSessions();
        renderConversationList();
        if (chatTitleText) chatTitleText.textContent = cleanTitle;
      }
    } catch (err) { /* silent fail */ }
  }

  function renderMessage(role, text, timestamp) {
    removeChatWelcome();
    if (!chatMessages) return null;

    const row = document.createElement("div");
    row.className = "page-chat-msg-row " + (role === "user" ? "page-chat-msg-row--user" : "page-chat-msg-row--ai");
    
    // Use 24h format for message time. If timestamp provided (e.g. from history), use it.
    const dateObj = timestamp ? new Date(timestamp) : new Date();
    const hours = String(dateObj.getHours()).padStart(2, "0");
    const minutes = String(dateObj.getMinutes()).padStart(2, "0");
    const timeStr = hours + ":" + minutes;

    const _name = (userData && typeof userData.name === "string" && userData.name.trim()) ? userData.name.trim() : "Workspace User";
    const _initials = (userData && typeof userData.initials === "string" && userData.initials.trim()) ? userData.initials.trim() : _name.charAt(0);
    const initials = role === "user" ? (_initials || "U") : "AI";
    const bubbleId = "bubble-" + Date.now();

    row.innerHTML =
      '<div class="avatar avatar-sm ' +
      (role === "ai" ? "avatar-ai" : "") +
      '">' +
      escapeHtml(initials) +
      '</div>' +
      '<div class="page-chat-msg-body">' +
      '<div class="page-chat-msg-bubble" id="' +
      bubbleId +
      '">' +
      formatText(text) +
      '</div>' +
      '<div class="page-chat-msg-meta">' +
      (role === "ai" ? escapeHtml(getCurrentModelLabel()) + " · " : "") +
      escapeHtml(timeStr) +
      '<div class="page-chat-msg-actions">' +
      '<button class="page-chat-msg-action-btn" onclick="copyMsg(this)" title="Copy">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
      '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>' +
      "</svg></button></div></div></div>";
    chatMessages.appendChild(row);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return document.getElementById(bubbleId);
  }

  function showTyping(sessionId) {
    if (!chatMessages || sessionId !== state.sessionId) return;
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
      "</div>";
    chatMessages.appendChild(row);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function removeTyping(sessionId) {
    const el = document.getElementById(getTypingIndicatorId(sessionId));
    if (el) el.remove();
  }

  function autoResize(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }

  function getCurrentModel() {
    return state.models[state.modelIndex] || state.models[0];
  }

  function getCurrentModelLabel() {
    const m = getCurrentModel();
    return m.display_name || (m.provider + " (" + m.model + ")");
  }

  function isSessionPending(sessionId) {
    return !!state.pendingSessions[sessionId];
  }

  function isCurrentSessionPending() {
    return isSessionPending(state.sessionId);
  }

  function syncComposerState() {
    if (!sendBtn) return;
    const hasText = !!(chatInput && chatInput.value.trim());
    sendBtn.disabled = !hasText || isCurrentSessionPending();
  }

  function getTypingIndicatorId(sessionId) {
    return "typingIndicator-" + String(sessionId || "default").replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  function getOrCreatePendingState(sessionId) {
    if (!state.pendingSessions[sessionId]) {
      state.pendingSessions[sessionId] = {
        text: "",
        firstChunkReceived: false,
        completed: false,
        error: "",
        bubbleEl: null
      };
    }
    return state.pendingSessions[sessionId];
  }

  function showPendingBubble(sessionId, isFinal) {
    const pending = state.pendingSessions[sessionId];
    if (!pending || sessionId !== state.sessionId || !chatMessages) return;

    let bubble = pending.bubbleEl;
    if (!bubble || !chatMessages.contains(bubble)) {
      bubble = renderMessage("ai", pending.text || "");
      pending.bubbleEl = bubble;
    }

    const row = bubble.closest(".page-chat-msg-row");
    if (row) row.style.display = "";

    bubble.innerHTML = formatText(pending.text || "") + (isFinal ? "" : '<span class="page-chat-cursor"></span>');
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function restorePendingSessionUI(sessionId) {
    const pending = state.pendingSessions[sessionId];
    if (!pending || sessionId !== state.sessionId) return;

    if (pending.firstChunkReceived || pending.text) {
      removeTyping(sessionId);
      showPendingBubble(sessionId, !!pending.completed);
      return;
    }

    if (!pending.completed && !pending.error) {
      showTyping(sessionId);
    }
  }

  function resetSession() {
    state.sessionId = "web-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("kway_chat_session", state.sessionId);
    state.msgCount = 0;
    state.tokenCount = 0;
    state.startAt = Date.now();
    state.meetingText = "";
    // Session will be added to sidebar lazily via ensureSessionExists()
    // when user sends their first message — not on creation.
    renderConversationList();
    updateStats();
    syncComposerState();
  }

  async function loadModels() {
    try {
      const res = await fetch("/api/models");
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.status === "success" && Array.isArray(data.models) && data.models.length > 0) {
        state.models = data.models;
      }
    } catch (_err) {
      // Keep default model if API unavailable.
    }
    if (modelName) modelName.textContent = getCurrentModelLabel();

    // Prioritize model from kway_settings if available
    const raw = localStorage.getItem("kway_settings");
    if (raw) {
      try {
        const settings = JSON.parse(raw);
        if (settings.model) {
          const idx = state.models.findIndex(m => m.model === settings.model);
          if (idx !== -1) {
            state.modelIndex = idx;
            if (modelName) modelName.textContent = getCurrentModelLabel();
          }
        }
      } catch (_err) { /* ignore */ }
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
          for (const pair of entries) {
            const meta = pair[1] || {};
            html +=
              '<div class="page-chat-info-card">' +
              '<div class="page-chat-info-card-title">' +
              escapeHtml(pair[0]) +
              "</div>" +
              '<div class="page-chat-info-card-desc">' +
              escapeHtml(meta.description || "No description") +
              "</div></div>";
          }
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
              "</span></div>"
          );
        }
      }
    } catch (_err) {
      // Non-blocking side panel enhancement.
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch("/chat/session/" + encodeURIComponent(state.sessionId));
      if (!res.ok) return false;
      const data = await res.json();
      const history = data.history || [];
      if (!Array.isArray(history) || history.length === 0) {
        return false;
      }
      removeChatWelcome();
      for (const msg of history) {
        if (!msg || !msg.role || typeof msg.content !== "string") continue;
        const role = msg.role === "assistant" ? "ai" : "user";
        // History messages should have timestamps from backend if available
        renderMessage(role, msg.content, msg.created_at ? msg.created_at * 1000 : null);
        state.msgCount += 1;
        state.tokenCount += Math.ceil(msg.content.length / 4);
      }
      updateStats();
      return true;
    } catch (_err) {
      return false;
    }
  }

  async function handleApproval(toolName, riskDesc, sessionId, pending) {
    const existing = document.getElementById("authApprovalModal");
    if (existing) existing.remove();

    return new Promise((resolve) => {
      const modal = document.createElement("div");
      modal.id = "authApprovalModal";
      modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:9999;";
      modal.innerHTML =
        '<div style="background:var(--bg-surface,#1e1e2e);border-radius:12px;max-width:460px;width:92%;box-shadow:0 8px 32px rgba(0,0,0,.4);">' +
        '<div style="background:linear-gradient(135deg,#f05252,#d03030);border-radius:12px 12px 0 0;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;">' +
        '<div><p style="color:#fff;margin:0;font-weight:600;font-size:1rem;">⚠ 高風險操作授權請求</p>' +
        '<p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:.82rem;">需要您的確認才能繼續執行</p></div>' +
        '<button id="authModalCloseBtn" style="background:none;border:none;color:#fff;font-size:1.1rem;cursor:pointer;padding:4px;">✕</button></div>' +
        '<div style="padding:20px;">' +
        '<p style="margin:0 0 6px;font-weight:600;font-size:.85rem;color:var(--text-secondary,#aaa);">技能名稱</p>' +
        '<p style="margin:0 0 14px;font-family:monospace;background:var(--bg-base,#13131f);padding:8px 12px;border-radius:8px;color:#60a5fa;font-size:.9rem;">' + toolName + "</p>" +
        '<p style="margin:0 0 6px;font-weight:600;font-size:.85rem;color:var(--text-secondary,#aaa);">風險說明</p>' +
        '<p style="margin:0;color:var(--text-secondary,#aaa);font-size:.88rem;line-height:1.55;">' + riskDesc + "</p></div>" +
        '<div style="display:flex;gap:10px;justify-content:flex-end;padding:14px 20px;border-top:1px solid var(--border-subtle,rgba(255,255,255,.08));">' +
        '<button id="authRejectBtn" style="padding:8px 20px;border-radius:8px;border:1px solid var(--border-subtle,rgba(255,255,255,.15));background:none;color:var(--text-primary,#e0e0e0);cursor:pointer;font-size:.9rem;">拒絕執行</button>' +
        '<button id="authApproveBtn" style="padding:8px 20px;border-radius:8px;border:none;background:#f05252;color:#fff;cursor:pointer;font-size:.9rem;font-weight:600;">確認授權</button></div></div>';
      document.body.appendChild(modal);

      function closeModal() { modal.remove(); }

      async function doReject() {
        closeModal();
        try { await fetch("/chat/reject/" + encodeURIComponent(sessionId), { method: "POST" }); } catch (_) {}
        pending.text = (pending.text ? pending.text + "\n\n" : "") + "⚠ 已拒絕執行高風險技能「" + toolName + "」。";
        pending.completed = true;
        showPendingBubble(sessionId, true);
        resolve(pending.text);
      }

      async function doApprove() {
        closeModal();
        try {
          const res2 = await fetch("/chat/approve/" + encodeURIComponent(sessionId), { method: "POST" });
          if (!res2.ok) {
            const errText = await res2.text();
            pending.text = (pending.text ? pending.text + "\n\n" : "") + "⚠ 授權恢復失敗: " + errText;
            pending.completed = true;
            showPendingBubble(sessionId, true);
            resolve(pending.text);
            return;
          }
          pending.text = "";
          const reader2 = res2.body.getReader();
          const dec2 = new TextDecoder("utf-8");
          let buf2 = "";
          while (true) {
            const r = await reader2.read();
            if (r.done) break;
            buf2 += dec2.decode(r.value, { stream: true });
            const evts = buf2.split("\r\n\r\n");
            buf2 = evts.pop() || "";
            for (const ev of evts) {
              for (const ln of ev.split(/\r?\n/)) {
                if (!ln.startsWith("data: ")) continue;
                const pl = ln.slice(6).trim();
                if (pl === "[DONE]") continue;
                let p2 = null;
                try { p2 = JSON.parse(pl); } catch (_) { continue; }
                if (p2.status === "streaming") {
                  pending.text += p2.content || "";
                  showPendingBubble(sessionId, false);
                } else if (p2.status === "success") {
                  pending.text = p2.content || pending.text;
                  pending.completed = true;
                  showPendingBubble(sessionId, true);
                }
              }
            }
          }
          resolve(pending.text);
        } catch (err) {
          pending.text = (pending.text ? pending.text + "\n\n" : "") + "⚠ 授權執行失敗: " + (err.message || "");
          pending.completed = true;
          showPendingBubble(sessionId, true);
          resolve(pending.text);
        }
      }

      modal.querySelector("#authModalCloseBtn").onclick = doReject;
      modal.querySelector("#authRejectBtn").onclick = doReject;
      modal.querySelector("#authApproveBtn").onclick = doApprove;
    });
  }

  async function streamChatResponse(res, sessionId) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const pending = getOrCreatePendingState(sessionId);
    let buffer = "";

    while (true) {
      const read = await reader.read();
      if (read.done) break;
      buffer += decoder.decode(read.value, { stream: true });
      const events = buffer.split("\r\n\r\n");
      buffer = events.pop() || "";
      for (const event of events) {
        const lines = event.split(/\r?\n/);
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6).trim();
          if (payload === "[DONE]") continue;

          let parsed = null;
          try {
            parsed = JSON.parse(payload);
          } catch (_err) {
            continue;
          }

          if (parsed.status === "streaming" || parsed.status === "success") {
            pending.firstChunkReceived = true;
            removeTyping(sessionId);
          }

          if (parsed.status === "streaming") {
            pending.text += parsed.content || "";
            showPendingBubble(sessionId, false);
          } else if (parsed.status === "success") {
            pending.text = parsed.content || pending.text;
            pending.completed = true;
            showPendingBubble(sessionId, true);
          } else if (parsed.status === "error") {
            throw new Error(parsed.message || "Server error");
          } else if (parsed.status === "tool_call") {
            if (!pending.firstChunkReceived) {
              pending.firstChunkReceived = true;
              removeTyping(sessionId);
            }
            pending.text = "⚙ " + (parsed.message || ("正在執行技能：" + (parsed.tool_name || "...")));
            showPendingBubble(sessionId, false);
          } else if (parsed.status === "requires_approval") {
            if (!pending.firstChunkReceived) {
              pending.firstChunkReceived = true;
              removeTyping(sessionId);
            }
            try { reader.cancel(); } catch (_) {}
            return await handleApproval(
              parsed.tool_name || "未知技能",
              parsed.risk_description || "此操作被標記為高風險，需要您的授權。",
              sessionId,
              pending
            );
          }
        }
      }
    }

    removeTyping(sessionId);
    if (pending.text) {
      showPendingBubble(sessionId, !!pending.completed);
    }
    return pending.text;
  }

  async function sendMessage(text) {
    const content = (text || "").trim();
    const requestSessionId = state.sessionId;
    if (!content || isSessionPending(requestSessionId)) return;

    if (requestSessionId === state.sessionId && chatInput) {
      chatInput.value = "";
      autoResize(chatInput);
    }
    syncComposerState();

    // Lazy session creation: only add to sidebar when user actually sends a message
    ensureSessionExists();

    renderMessage("user", content);
    state.msgCount += 1;
    updateStats(Math.ceil(content.length / 4));
    const pending = getOrCreatePendingState(requestSessionId);
    pending.text = "";
    pending.firstChunkReceived = false;
    pending.completed = false;
    pending.error = "";
    pending.bubbleEl = null;
    showTyping(requestSessionId);
    updateCurrentSessionPreview(content);

    try {
      const m = getCurrentModel();
      const rawSettings = localStorage.getItem("kway_settings");
      let language = "繁體中文";
      let detail_level = "適中";
      if (rawSettings) {
        try {
          const s = JSON.parse(rawSettings);
          language = s.language || language;
          detail_level = s.detail || detail_level;
        } catch(_e) {}
      }

      const payload = {
        user_input: content,
        session_id: requestSessionId,
        provider: m.provider || "openai",
        model: m.model || "gpt-4o",
        language: language,
        detail_level: detail_level
      };
      console.log("[Chat] Sending payload:", payload);
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

      const finalText = await streamChatResponse(res, requestSessionId);
      delete state.pendingSessions[requestSessionId];
      if (requestSessionId === state.sessionId) {
        state.msgCount += 1;
        updateStats(Math.ceil(finalText.length / 4));
        state.meetingText += "\n\nUser:\n" + content + "\n\nAssistant:\n" + finalText;
      }
      
      // Trigger title summarization on first exchange
      if (requestSessionId === state.sessionId && state.msgCount <= 2) {
        summarizeConversationTitle(content, finalText);
      }
    } catch (err) {
      removeTyping(requestSessionId);
      const errorText = "Request failed\n\n" + (err.message || "");
      const pendingError = getOrCreatePendingState(requestSessionId);
      pendingError.text = errorText;
      pendingError.error = errorText;
      pendingError.completed = true;
      pendingError.firstChunkReceived = true;
      pendingError.bubbleEl = null;
      if (requestSessionId !== state.sessionId) {
        showToast("Chat request failed", "error");
        return;
      }
      renderMessage("ai", "系統暫時無法回覆，請稍後再試。\n\n" + (err.message || ""));
      showToast("Chat request failed", "error");
      delete state.pendingSessions[requestSessionId];
    } finally {
      syncComposerState();
    }
  }

  window.sendSuggestion = function (text) {
    if (chatInput) chatInput.value = text;
    sendMessage(text);
  };

  window.switchTab = function (btn, name) {
    document.querySelectorAll(".page-chat-tab-btn").forEach(function (b) {
      b.classList.remove("is-active");
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
    resetSession();
    if (chatMessages) {
      chatMessages.innerHTML =
        '<div class="page-chat-welcome" id="chatWelcome"><div class="page-chat-welcome-logo"><img src="../assets/images/kw_logo.png" width="56" alt="Logo"></div><h2>新對話已就緒</h2><p>請輸入您的問題開始對話。</p></div>';
    }
    if (chatTitleText) chatTitleText.textContent = "新對話";
    showToast("已建立新對話", "success");
  };

  window.clearConversation = window.newConversation;

  window.confirmDeleteCurrentConversation = function() {
    const modal = document.getElementById("deleteModal");
    const confirmBtn = document.getElementById("confirmDeleteBtn");
    if (!modal || !confirmBtn) return;
    
    modal.style.display = "flex";
    confirmBtn.onclick = function() {
      deleteCurrentConversation();
      modal.style.display = "none";
    };
  };

  window.closeDeleteModal = function() {
    const modal = document.getElementById("deleteModal");
    if (modal) modal.style.display = "none";
  };

  function deleteCurrentConversation() {
    const idx = state.sessions.findIndex(s => s.id === state.sessionId);
    if (idx !== -1) {
      state.sessions.splice(idx, 1);
      saveSessions();
      if (state.sessions.length > 0) {
        const nextSid = state.sessions[state.sessions.length - 1].id;
        window.loadConversationById(nextSid);
      } else {
        window.newConversation();
      }
    } else {
      window.newConversation();
    }
  }

  window.loadConversationById = async function (sid) {
    if (sid === state.sessionId) return;
    state.sessionId = sid;
    localStorage.setItem("kway_chat_session", state.sessionId);
    syncComposerState();
    
    const session = state.sessions.find(s => s.id === sid);
    if (chatTitleText) chatTitleText.textContent = session ? session.title : "MCP Assistant";
    
    if (chatMessages) chatMessages.innerHTML = "";
    state.msgCount = 0;
    state.tokenCount = 0;
    state.meetingText = "";
    
    renderConversationList();
    const hasHistory = await loadHistory();
    restorePendingSessionUI(state.sessionId);
    if (!hasHistory) {
      if (chatMessages) {
        chatMessages.innerHTML =
          '<div class="page-chat-welcome" id="chatWelcome"><div class="page-chat-welcome-logo"><img src="../assets/images/kw_logo.png" width="56" alt="Logo"></div><h2>對話已載入</h2><p>此對話尚無訊息，請輸入問題開始。</p></div>';
      }
    }
    showToast("對話已載入", "success");
  };

  window.loadConversation = function (idx) {
    // Legacy support or fallback
    const session = state.sessions[idx];
    if (session) window.loadConversationById(session.id);
  };

  window.triggerAudioUpload = function () {
    const audioFileInput = document.getElementById("audioFileInput");
    if (!audioFileInput) return;
    audioFileInput.value = "";
    audioFileInput.onchange = async function () {
      const file = audioFileInput.files[0];
      if (!file) return;

      showToast("音訊檔案上傳中…", "info");

      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("/workspace/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (data.status !== "success") throw new Error(data.detail || "上傳失敗");

        const msg = `幫我將這個音訊檔案轉換為逐字稿，file_path: ${data.filepath}`;
        if (chatInput) chatInput.value = msg;
        showToast(`已上傳：${file.name}`, "success");
        sendMessage(msg);
      } catch (err) {
        showToast("音訊上傳失敗：" + err.message, "error");
      }
    };
    audioFileInput.click();
  };

  if (chatInput && sendBtn) {
    chatInput.addEventListener("input", function () {
      autoResize(chatInput);
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
  const safeName = (userData && typeof userData.name === "string" && userData.name.trim()) ? userData.name.trim() : "Workspace User";
  const safeInitials = (userData && typeof userData.initials === "string" && userData.initials.trim()) ? userData.initials.trim() : safeName.charAt(0);

  function setAvatar(el) {
    if (!el) return;

    const pic = (userData && typeof userData.picture === "string" && userData.picture.trim()) ? userData.picture.trim() : "";
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
        // Fallback to initials if image fails to load
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

  // Hydrate LINE login user from server cookie (if present), then proceed.
  hydrateAuthFromServer().finally(function () {
    loadSideInfo();
    renderConversationList();
    loadHistory();
    syncComposerState();
  });
})();
