(function () {
  "use strict";

  const state = {
    msgCount: 0,
    tokenCount: 0,
    startAt: Date.now(),
    waiting: false,
    models: [{ provider: "openai", model: "gpt-4o", display_name: "OpenAI (gpt-4o)" }],
    modelIndex: 0,
    // Always start a fresh web session when chat page loads.
    sessionId: "web-" + Math.random().toString(36).slice(2, 10),
    meetingText: "",
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

  function renderMessage(role, text) {
    removeChatWelcome();
    if (!chatMessages) return null;

    const row = document.createElement("div");
    row.className = "page-chat-msg-row " + (role === "user" ? "page-chat-msg-row--user" : "page-chat-msg-row--ai");
    const time = new Date().toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" });
    const initials = role === "user" ? (userData.initials || userData.name.charAt(0) || "U") : "AI";
    const bubbleId = "bubble-" + Date.now();

    row.innerHTML =
      '<div class="avatar avatar-sm ' +
      (role === "ai" ? "avatar-ai" : "") +
      '">' +
      escapeHtml(initials) +
      "</div>" +
      '<div class="page-chat-msg-body">' +
      '<div class="page-chat-msg-bubble" id="' +
      bubbleId +
      '">' +
      formatText(text || "") +
      "</div>" +
      '<div class="page-chat-msg-meta">' +
      (role === "ai" ? escapeHtml(getCurrentModelLabel()) + " · " : "") +
      escapeHtml(time) +
      '<div class="page-chat-msg-actions">' +
      '<button class="page-chat-msg-action-btn" onclick="copyMsg(this)" title="Copy">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
      '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>' +
      "</svg></button></div></div></div>";
    chatMessages.appendChild(row);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return document.getElementById(bubbleId);
  }

  function showTyping() {
    if (!chatMessages) return;
    const row = document.createElement("div");
    row.className = "page-chat-typing-row";
    row.id = "typingIndicator";
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

  function removeTyping() {
    const el = document.getElementById("typingIndicator");
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

  function resetSession() {
    state.sessionId = "web-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("kway_chat_session", state.sessionId);
    state.msgCount = 0;
    state.tokenCount = 0;
    state.startAt = Date.now();
    state.meetingText = "";
    updateStats();
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
      if (!res.ok) return;
      const data = await res.json();
      const history = data.history || [];
      if (!Array.isArray(history) || history.length === 0) return;
      removeChatWelcome();
      for (const msg of history) {
        if (!msg || !msg.role || typeof msg.content !== "string") continue;
        const role = msg.role === "assistant" ? "ai" : "user";
        renderMessage(role, msg.content);
        state.msgCount += 1;
        state.tokenCount += Math.ceil(msg.content.length / 4);
      }
      updateStats();
    } catch (_err) {
      // Ignore history failures.
    }
  }

  async function streamChatResponse(res, bubbleEl) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let full = "";
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
          if (parsed.status === "streaming") {
            const delta = parsed.content || "";
            full += delta;
            bubbleEl.innerHTML = formatText(full) + '<span class="page-chat-cursor"></span>';
            chatMessages.scrollTop = chatMessages.scrollHeight;
          } else if (parsed.status === "success") {
            const finalText = parsed.content || full;
            full = finalText;
            bubbleEl.innerHTML = formatText(finalText);
          } else if (parsed.status === "error") {
            throw new Error(parsed.message || "Server error");
          }
        }
      }
    }
    bubbleEl.innerHTML = formatText(full);
    return full;
  }

  async function sendMessage(text) {
    const content = (text || "").trim();
    if (!content || state.waiting) return;

    state.waiting = true;
    if (sendBtn) sendBtn.disabled = true;
    if (chatInput) {
      chatInput.value = "";
      autoResize(chatInput);
    }

    renderMessage("user", content);
    state.msgCount += 1;
    updateStats(Math.ceil(content.length / 4));
    showTyping();

    try {
      const m = getCurrentModel();
      const payload = {
        user_input: content,
        session_id: state.sessionId,
        provider: m.provider || "openai",
        model: m.model || "gpt-4o",
      };
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      removeTyping();
      if (!res.ok) {
        const errText = await res.text();
        throw new Error("HTTP " + res.status + ": " + errText);
      }

      const bubble = renderMessage("ai", "");
      const finalText = await streamChatResponse(res, bubble);
      state.msgCount += 1;
      updateStats(Math.ceil(finalText.length / 4));
      state.meetingText += "\n\nUser:\n" + content + "\n\nAssistant:\n" + finalText;
    } catch (err) {
      removeTyping();
      renderMessage("ai", "系統暫時無法回覆，請稍後再試。\n\n" + (err.message || ""));
      showToast("Chat request failed", "error");
    } finally {
      state.waiting = false;
      if (sendBtn) sendBtn.disabled = false;
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

  window.newConversation = async function () {
    try {
      await fetch("/chat/session/" + encodeURIComponent(state.sessionId), { method: "DELETE" });
    } catch (_err) {
      // Ignore reset error.
    }
    resetSession();
    if (chatMessages) {
      chatMessages.innerHTML =
        '<div class="page-chat-welcome" id="chatWelcome"><h2>MCP Chat Ready</h2><p>Start a new conversation.</p></div>';
    }
    showToast("New conversation created", "success");
  };

  window.clearConversation = window.newConversation;

  window.loadConversation = function (idx) {
    document.querySelectorAll(".page-chat-conv-item").forEach(function (el, i) {
      el.classList.toggle("is-active", i === idx);
    });
    const titles = ["MCP Assistant", "Q4 Report", "Integration Plan", "System Review"];
    if (chatTitleText) chatTitleText.textContent = titles[idx] || "MCP Assistant";
  };

  window.triggerAudioUpload = function () {
    showToast("Audio pipeline is not enabled in MCP mode", "info");
  };

  if (chatInput && sendBtn) {
    chatInput.addEventListener("input", function () {
      autoResize(chatInput);
      sendBtn.disabled = !chatInput.value.trim() || state.waiting;
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
  if (topbarAvatar) topbarAvatar.textContent = userData.initials || userData.name.charAt(0) || "U";
  if (sidebarAvatar) sidebarAvatar.textContent = userData.initials || userData.name.charAt(0) || "U";
  if (sidebarName) sidebarName.textContent = userData.name || "Workspace User";
  if (sidebarDept) sidebarDept.textContent = (userData.dept || "MCP Workspace") + " · Connected";

  setInterval(updateSessionDuration, 1000);
  updateSessionDuration();
  updateStats();
  loadModels();
  loadSideInfo();
  loadHistory();
})();
