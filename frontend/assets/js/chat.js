(function () {
  "use strict";

  const ACTIVE_TASK_STATUSES = new Set(["running", "tool_call", "requires_approval", "approved"]);
  const TERMINAL_TASK_STATUSES = new Set(["completed", "error", "rejected", "cancelled"]);

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
    userDocuments: [],
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
    sessionPendingAudioFile: {},     // sessionId -> { path: string, name: string }
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
  const audioUploadBtn = document.getElementById("audioUploadBtn");
  const audioRecorderBtn = document.getElementById("audioRecorderBtn");
  const audioFileInput = document.getElementById("audioFileInput");
  const modelName = document.getElementById("modelName");
  const chatTitleText = document.getElementById("chatTitleText");
  const chatRoot = document.querySelector(".page-chat-root");
  const chatBody = document.getElementById("chatBody");
  const chatMobileOverlay = document.getElementById("chatMobileOverlay");
  const chatPrimaryNav = document.getElementById("chatPrimaryNav");
  const chatSidebarLeft = document.getElementById("chatSidebarLeft");
  const primaryNavToggleButtons = [
    document.getElementById("chatPrimaryNavToggle"),
    document.getElementById("chatPrimaryNavInlineToggle"),
  ].filter(Boolean);
  const leftPanelToggleButtons = [document.getElementById("chatSidebarLeftToggle")].filter(Boolean);
  const rightPanelToggleButtons = [document.getElementById("chatSidebarRightToggle")].filter(Boolean);
  const rightPanelCloseButton = document.getElementById("chatSidebarRightCloseBtn");
  const columnResizerHandles = Array.from(document.querySelectorAll(".page-chat-column-resizer"));
  const PRIMARY_NAV_COLLAPSED_KEY = "kway_chat_primary_nav_collapsed";
  const CHAT_DESKTOP_LAYOUT_KEY = "kway_chat_desktop_layout";
  const DESKTOP_LAYOUT_MEDIA = window.matchMedia("(min-width: 1101px)");
  const RIGHT_PANEL_DRAWER_MEDIA = window.matchMedia("(max-width: 1280px)");
  const SIDE_PANEL_DRAWER_MEDIA = window.matchMedia("(max-width: 1024px)");
  const DEFAULT_DESKTOP_LAYOUT = { nav: 96, left: 240, right: 276 };
  const MIN_DESKTOP_LAYOUT = { nav: 72, left: 200, right: 220, main: 420 };
  const MAX_DESKTOP_LAYOUT = { nav: 180, left: 420, right: 420 };
  let isPrimaryNavCollapsed = true;
  let desktopLayoutWidths = { ...DEFAULT_DESKTOP_LAYOUT };
  let activeColumnResize = null;
  let openResponsivePanel = null;
  const initialWelcomeMarkup = (() => {
    const staticWelcome = document.getElementById("chatWelcome");
    if (!staticWelcome) return "";
    const markup = staticWelcome.innerHTML;
    staticWelcome.remove();
    return markup;
  })();
  const audioRecorderState = {
    supported:
      !!(navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === "function")
      && typeof window.MediaRecorder !== "undefined",
    isRecording: false,
    isProcessing: false,
    stream: null,
    mediaRecorder: null,
    chunks: [],
    startedAt: 0,
    mimeType: "",
    fileExtension: "webm",
    monitorContext: null,
    monitorSource: null,
    monitorAnalyser: null,
    monitorTimerId: 0,
    signalPeak: 0,
    inputLabel: "",
  };

  function readStoredBoolean(key, fallbackValue) {
    const stored = localStorage.getItem(key);
    if (stored === null) return fallbackValue;
    return stored === "1";
  }

  function clamp(value, minValue, maxValue) {
    return Math.min(Math.max(value, minValue), maxValue);
  }

  function sanitizeWidth(value, fallbackValue) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return fallbackValue;
    return numeric;
  }

  function resolveRecordedAudioMimeType() {
    if (typeof window.MediaRecorder === "undefined" || typeof window.MediaRecorder.isTypeSupported !== "function") {
      return "";
    }
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/mp4",
      "audio/ogg;codecs=opus",
      "audio/ogg",
    ];
    return candidates.find((candidate) => window.MediaRecorder.isTypeSupported(candidate)) || "";
  }

  function inferAudioExtensionFromMimeType(mimeType) {
    const normalized = String(mimeType || "").toLowerCase();
    if (normalized.indexOf("mp4") !== -1 || normalized.indexOf("m4a") !== -1 || normalized.indexOf("aac") !== -1) {
      return "m4a";
    }
    if (normalized.indexOf("ogg") !== -1) {
      return "ogg";
    }
    if (normalized.indexOf("wav") !== -1) {
      return "wav";
    }
    return "webm";
  }

  function buildRecordedAudioFilename(extension) {
    const now = new Date();
    const yyyy = String(now.getFullYear());
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");
    const hh = String(now.getHours()).padStart(2, "0");
    const min = String(now.getMinutes()).padStart(2, "0");
    const sec = String(now.getSeconds()).padStart(2, "0");
    return "recording-" + yyyy + mm + dd + "-" + hh + min + sec + "." + (extension || "webm");
  }

  function createRecordedAudioFile(blob, filename) {
    if (typeof window.File === "function") {
      return new File([blob], filename, { type: blob.type || "audio/webm" });
    }
    blob.name = filename;
    return blob;
  }

  async function waitForAudioTrackReady(track, timeoutMs) {
    if (!track || !track.muted) return;
    const waitMs = Math.max(0, Number(timeoutMs || 1200));
    await new Promise(function (resolve) {
      let settled = false;
      let timerId = 0;

      function finish() {
        if (settled) return;
        settled = true;
        if (timerId) window.clearTimeout(timerId);
        track.removeEventListener("unmute", finish);
        resolve();
      }

      track.addEventListener("unmute", finish, { once: true });
      timerId = window.setTimeout(finish, waitMs);
    });
  }

  function stopAudioSignalMonitor() {
    if (audioRecorderState.monitorTimerId) {
      window.clearInterval(audioRecorderState.monitorTimerId);
      audioRecorderState.monitorTimerId = 0;
    }
    if (audioRecorderState.monitorSource) {
      try {
        audioRecorderState.monitorSource.disconnect();
      } catch (_err) {
        // ignore disconnect errors
      }
      audioRecorderState.monitorSource = null;
    }
    if (audioRecorderState.monitorAnalyser) {
      try {
        audioRecorderState.monitorAnalyser.disconnect();
      } catch (_err) {
        // ignore disconnect errors
      }
      audioRecorderState.monitorAnalyser = null;
    }
    if (audioRecorderState.monitorContext) {
      try {
        audioRecorderState.monitorContext.close();
      } catch (_err) {
        // ignore close errors
      }
      audioRecorderState.monitorContext = null;
    }
  }

  function startAudioSignalMonitor(stream) {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    audioRecorderState.signalPeak = 0;
    if (!AudioContextCtor || !stream) return;

    try {
      const ctx = new AudioContextCtor();
      if (typeof ctx.resume === "function") {
        ctx.resume().catch(function () {
          // ignore resume errors and keep best-effort monitoring
        });
      }
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      audioRecorderState.monitorContext = ctx;
      audioRecorderState.monitorSource = source;
      audioRecorderState.monitorAnalyser = analyser;

      const sampleFloat = typeof analyser.getFloatTimeDomainData === "function";
      const floatBuffer = sampleFloat ? new Float32Array(analyser.fftSize) : null;
      const byteBuffer = sampleFloat ? null : new Uint8Array(analyser.fftSize);

      audioRecorderState.monitorTimerId = window.setInterval(function () {
        let peak = 0;
        if (floatBuffer) {
          analyser.getFloatTimeDomainData(floatBuffer);
          for (let i = 0; i < floatBuffer.length; i += 1) {
            const level = Math.abs(floatBuffer[i]);
            if (level > peak) peak = level;
          }
        } else if (byteBuffer) {
          analyser.getByteTimeDomainData(byteBuffer);
          for (let i = 0; i < byteBuffer.length; i += 1) {
            const level = Math.abs((byteBuffer[i] - 128) / 128);
            if (level > peak) peak = level;
          }
        }
        if (peak > audioRecorderState.signalPeak) {
          audioRecorderState.signalPeak = peak;
        }
      }, 120);
    } catch (_err) {
      stopAudioSignalMonitor();
    }
  }

  function releaseAudioRecorderStream() {
    if (!audioRecorderState.stream) return;
    audioRecorderState.stream.getTracks().forEach((track) => {
      try {
        track.stop();
      } catch (_err) {
        // ignore track stop errors
      }
    });
    audioRecorderState.stream = null;
  }

  function resetAudioRecorderState() {
    stopAudioSignalMonitor();
    releaseAudioRecorderStream();
    audioRecorderState.isRecording = false;
    audioRecorderState.isProcessing = false;
    audioRecorderState.mediaRecorder = null;
    audioRecorderState.chunks = [];
    audioRecorderState.startedAt = 0;
    audioRecorderState.mimeType = "";
    audioRecorderState.fileExtension = "webm";
    audioRecorderState.signalPeak = 0;
    audioRecorderState.inputLabel = "";
  }

  function explainRecorderError(err) {
    const name = err && err.name ? String(err.name) : "";
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return "尚未取得麥克風權限，請允許瀏覽器使用麥克風後再試一次。";
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "找不到可用的麥克風裝置，請確認手機、平板或電腦已接上麥克風。";
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return "麥克風目前被其他程式占用，請關閉其他錄音應用後再試。";
    }
    if (name === "SecurityError") {
      return "目前頁面環境不允許直接錄音，請改用安全連線或既有的音檔上傳。";
    }
    return "無法啟動錄音功能，請稍後再試或改用音檔上傳。";
  }

  function syncAudioRecorderButton() {
    if (!audioRecorderBtn) return;
    const isUnsupported = !audioRecorderState.supported;
    const isRecording = !!audioRecorderState.isRecording;
    const isProcessing = !!audioRecorderState.isProcessing;

    audioRecorderBtn.classList.toggle("is-recording", isRecording);
    audioRecorderBtn.classList.toggle("is-processing", isProcessing);
    audioRecorderBtn.disabled = isProcessing;
    audioRecorderBtn.setAttribute("aria-pressed", isRecording ? "true" : "false");

    if (isUnsupported) {
      audioRecorderBtn.setAttribute("aria-label", "目前瀏覽器不支援直接錄音");
      audioRecorderBtn.setAttribute("title", "目前瀏覽器不支援直接錄音");
      return;
    }
    if (isRecording) {
      audioRecorderBtn.setAttribute("aria-label", "停止錄音並決定是否儲存到文件中心");
      audioRecorderBtn.setAttribute("title", "錄音中，點一下停止並決定是否儲存到文件中心");
      return;
    }
    if (isProcessing) {
      audioRecorderBtn.setAttribute("aria-label", "正在處理錄音");
      audioRecorderBtn.setAttribute("title", "正在處理錄音");
      return;
    }
    audioRecorderBtn.setAttribute("aria-label", "開始錄音並可儲存到文件中心");
    audioRecorderBtn.setAttribute("title", "開始錄音並可儲存到文件中心");
  }

  function readStoredDesktopLayout() {
    try {
      const raw = localStorage.getItem(CHAT_DESKTOP_LAYOUT_KEY);
      if (!raw) return { ...DEFAULT_DESKTOP_LAYOUT };
      const parsed = JSON.parse(raw);
      return {
        nav: sanitizeWidth(parsed.nav, DEFAULT_DESKTOP_LAYOUT.nav),
        left: sanitizeWidth(parsed.left, DEFAULT_DESKTOP_LAYOUT.left),
        right: sanitizeWidth(parsed.right, DEFAULT_DESKTOP_LAYOUT.right),
      };
    } catch (_err) {
      return { ...DEFAULT_DESKTOP_LAYOUT };
    }
  }

  function normalizeDesktopLayout(widths) {
    const next = {
      nav: clamp(
        sanitizeWidth(widths && widths.nav, DEFAULT_DESKTOP_LAYOUT.nav),
        MIN_DESKTOP_LAYOUT.nav,
        MAX_DESKTOP_LAYOUT.nav
      ),
      left: clamp(
        sanitizeWidth(widths && widths.left, DEFAULT_DESKTOP_LAYOUT.left),
        MIN_DESKTOP_LAYOUT.left,
        MAX_DESKTOP_LAYOUT.left
      ),
      right: clamp(
        sanitizeWidth(widths && widths.right, DEFAULT_DESKTOP_LAYOUT.right),
        MIN_DESKTOP_LAYOUT.right,
        MAX_DESKTOP_LAYOUT.right
      ),
    };

    if (!chatBody) return next;

    const containerWidth = chatBody.getBoundingClientRect().width || 0;
    if (!DESKTOP_LAYOUT_MEDIA.matches || !containerWidth) return next;

    let overflow =
      next.nav + next.left + next.right - Math.max(containerWidth - MIN_DESKTOP_LAYOUT.main, 0);

    if (overflow > 0) {
      const shrinkOrder = [
        ["right", MIN_DESKTOP_LAYOUT.right],
        ["left", MIN_DESKTOP_LAYOUT.left],
        ["nav", MIN_DESKTOP_LAYOUT.nav],
      ];

      shrinkOrder.forEach(function (entry) {
        const key = entry[0];
        const minWidth = entry[1];
        if (overflow <= 0) return;
        const available = Math.max(0, next[key] - minWidth);
        const reduction = Math.min(available, overflow);
        next[key] -= reduction;
        overflow -= reduction;
      });
    }

    return next;
  }

  function clearDesktopLayoutStyles() {
    if (!chatRoot) return;
    chatRoot.style.removeProperty("--chat-nav-width-expanded");
    chatRoot.style.removeProperty("--chat-left-width");
    chatRoot.style.removeProperty("--chat-right-width");
  }

  function applyDesktopLayout(widths, options) {
    const opts = options || {};
    const normalized = normalizeDesktopLayout(widths || desktopLayoutWidths);
    desktopLayoutWidths = normalized;

    if (!DESKTOP_LAYOUT_MEDIA.matches) {
      clearDesktopLayoutStyles();
      return normalized;
    }

    if (chatRoot) {
      chatRoot.style.setProperty("--chat-nav-width-expanded", normalized.nav + "px");
      chatRoot.style.setProperty("--chat-left-width", normalized.left + "px");
      chatRoot.style.setProperty("--chat-right-width", normalized.right + "px");
    }

    if (opts.persist !== false) {
      localStorage.setItem(CHAT_DESKTOP_LAYOUT_KEY, JSON.stringify(normalized));
    }

    return normalized;
  }

  function getVisiblePrimaryNavWidth() {
    return isPrimaryNavCollapsed ? 0 : desktopLayoutWidths.nav;
  }

  function stopColumnResize() {
    if (!activeColumnResize) return;
    activeColumnResize = null;
    if (chatBody) {
      chatBody.classList.remove("is-layout-resizing");
    }
    window.removeEventListener("pointermove", handleColumnResizeMove);
    window.removeEventListener("pointerup", stopColumnResize);
    window.removeEventListener("pointercancel", stopColumnResize);
    applyDesktopLayout(desktopLayoutWidths);
  }

  function handleColumnResizeMove(event) {
    if (!activeColumnResize || !chatBody || !DESKTOP_LAYOUT_MEDIA.matches) return;

    const rect = chatBody.getBoundingClientRect();
    const pointerOffset = clamp(event.clientX - rect.left, 0, rect.width);
    const nextLayout = { ...desktopLayoutWidths };
    const visibleNavWidth = getVisiblePrimaryNavWidth();

    if (activeColumnResize.edge === "nav") {
      nextLayout.nav = pointerOffset;
    } else if (activeColumnResize.edge === "left") {
      nextLayout.left = pointerOffset - visibleNavWidth;
    } else if (activeColumnResize.edge === "right") {
      nextLayout.right = rect.width - pointerOffset;
    }

    applyDesktopLayout(nextLayout, { persist: false });
  }

  function startColumnResize(event) {
    if (!DESKTOP_LAYOUT_MEDIA.matches || !chatBody) return;

    const handle = event.currentTarget;
    const edge = handle && handle.dataset ? handle.dataset.resizeEdge : "";
    if (!edge) return;

    activeColumnResize = { edge: edge };
    chatBody.classList.add("is-layout-resizing");
    if (typeof handle.setPointerCapture === "function") {
      handle.setPointerCapture(event.pointerId);
    }

    window.addEventListener("pointermove", handleColumnResizeMove);
    window.addEventListener("pointerup", stopColumnResize);
    window.addEventListener("pointercancel", stopColumnResize);
    event.preventDefault();
  }

  function handleDesktopLayoutResize() {
    if (activeColumnResize) {
      stopColumnResize();
    }

    if (DESKTOP_LAYOUT_MEDIA.matches) {
      applyDesktopLayout(desktopLayoutWidths, { persist: false });
    } else {
      clearDesktopLayoutStyles();
    }

    syncResponsivePanels();
  }

  function usesResponsiveDrawer(panelName) {
    if (panelName === "right") return RIGHT_PANEL_DRAWER_MEDIA.matches;
    if (panelName === "left" || panelName === "nav") return SIDE_PANEL_DRAWER_MEDIA.matches;
    return false;
  }

  function updatePanelToggleButtons() {
    const navExpanded = usesResponsiveDrawer("nav")
      ? openResponsivePanel === "nav"
      : !isPrimaryNavCollapsed;

    primaryNavToggleButtons.forEach(function (button) {
      const usesDrawer = usesResponsiveDrawer("nav");
      button.setAttribute("aria-expanded", String(navExpanded));
      button.setAttribute("title", usesDrawer ? (navExpanded ? "關閉主選單" : "開啟主選單") : (navExpanded ? "隱藏主選單" : "展開主選單"));
      button.classList.toggle("is-active", navExpanded);
    });

    leftPanelToggleButtons.forEach(function (button) {
      const isOpen = openResponsivePanel === "left";
      button.setAttribute("aria-expanded", String(isOpen));
      button.setAttribute("title", isOpen ? "關閉對話記錄" : "開啟對話記錄");
      button.classList.toggle("is-active", isOpen);
    });

    rightPanelToggleButtons.forEach(function (button) {
      const isOpen = openResponsivePanel === "right";
      button.setAttribute("aria-expanded", String(isOpen));
      button.setAttribute("title", isOpen ? "關閉工作面板" : "開啟工作面板");
      button.classList.toggle("is-active", isOpen);
    });
  }

  function setResponsivePanel(panelName) {
    const nextPanel = panelName && usesResponsiveDrawer(panelName) ? panelName : null;
    openResponsivePanel = nextPanel;

    if (chatBody) {
      chatBody.classList.toggle("is-primary-nav-drawer-open", nextPanel === "nav");
      chatBody.classList.toggle("is-left-panel-open", nextPanel === "left");
      chatBody.classList.toggle("is-right-panel-open", nextPanel === "right");
    }

    if (chatMobileOverlay) {
      chatMobileOverlay.classList.toggle("is-open", !!nextPanel);
    }

    updatePanelToggleButtons();
  }

  function closeResponsivePanels() {
    setResponsivePanel(null);
  }

  function toggleResponsivePanel(panelName) {
    if (!usesResponsiveDrawer(panelName)) return;
    setResponsivePanel(openResponsivePanel === panelName ? null : panelName);
  }

  function syncResponsivePanels() {
    if (openResponsivePanel && !usesResponsiveDrawer(openResponsivePanel)) {
      closeResponsivePanels();
      return;
    }
    setResponsivePanel(openResponsivePanel);
  }

  function applyPrimaryNavCollapsed(nextCollapsed, options) {
    const opts = options || {};
    isPrimaryNavCollapsed = !!nextCollapsed;

    if (chatBody) {
      chatBody.classList.toggle("is-primary-nav-collapsed", isPrimaryNavCollapsed);
    }
    updatePanelToggleButtons();

    if (opts.persist !== false) {
      localStorage.setItem(PRIMARY_NAV_COLLAPSED_KEY, isPrimaryNavCollapsed ? "1" : "0");
    }
  }

  function togglePrimaryNav(forceCollapsed) {
    if (usesResponsiveDrawer("nav")) {
      const shouldOpen = typeof forceCollapsed === "boolean" ? !forceCollapsed : openResponsivePanel !== "nav";
      setResponsivePanel(shouldOpen ? "nav" : null);
      return;
    }

    const nextCollapsed =
      typeof forceCollapsed === "boolean" ? forceCollapsed : !isPrimaryNavCollapsed;
    applyPrimaryNavCollapsed(nextCollapsed);
  }

  window.togglePrimaryNav = togglePrimaryNav;

  primaryNavToggleButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      togglePrimaryNav();
    });
  });

  leftPanelToggleButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      toggleResponsivePanel("left");
    });
  });

  rightPanelToggleButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      toggleResponsivePanel("right");
    });
  });

  if (rightPanelCloseButton) {
    rightPanelCloseButton.addEventListener("click", function () {
      if (usesResponsiveDrawer("right")) {
        closeResponsivePanels();
      }
    });
  }

  if (chatMobileOverlay) {
    chatMobileOverlay.addEventListener("click", function () {
      closeResponsivePanels();
    });
  }

  if (chatPrimaryNav) {
    chatPrimaryNav.addEventListener("click", function (event) {
      if (usesResponsiveDrawer("nav") && event.target.closest(".page-chat-primary-nav-btn")) {
        closeResponsivePanels();
      }
    });
  }

  if (chatSidebarLeft) {
    chatSidebarLeft.addEventListener("click", function (event) {
      if (!usesResponsiveDrawer("left")) return;
      if (
        event.target.closest(".page-chat-conv-item") ||
        event.target.closest(".page-chat-new-btn") ||
        event.target.closest(".page-chat-sidebar-header .btn-icon")
      ) {
        closeResponsivePanels();
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && openResponsivePanel) {
      closeResponsivePanels();
    }
  });

  columnResizerHandles.forEach(function (handle) {
    handle.addEventListener("pointerdown", startColumnResize);
  });

  applyPrimaryNavCollapsed(readStoredBoolean(PRIMARY_NAV_COLLAPSED_KEY, true), { persist: false });
  desktopLayoutWidths = normalizeDesktopLayout(readStoredDesktopLayout());
  applyDesktopLayout(desktopLayoutWidths, { persist: false });
  window.addEventListener("resize", handleDesktopLayoutResize);
  if (typeof DESKTOP_LAYOUT_MEDIA.addEventListener === "function") {
    DESKTOP_LAYOUT_MEDIA.addEventListener("change", handleDesktopLayoutResize);
  } else if (typeof DESKTOP_LAYOUT_MEDIA.addListener === "function") {
    DESKTOP_LAYOUT_MEDIA.addListener(handleDesktopLayoutResize);
  }
  if (typeof RIGHT_PANEL_DRAWER_MEDIA.addEventListener === "function") {
    RIGHT_PANEL_DRAWER_MEDIA.addEventListener("change", syncResponsivePanels);
  } else if (typeof RIGHT_PANEL_DRAWER_MEDIA.addListener === "function") {
    RIGHT_PANEL_DRAWER_MEDIA.addListener(syncResponsivePanels);
  }
  if (typeof SIDE_PANEL_DRAWER_MEDIA.addEventListener === "function") {
    SIDE_PANEL_DRAWER_MEDIA.addEventListener("change", syncResponsivePanels);
  } else if (typeof SIDE_PANEL_DRAWER_MEDIA.addListener === "function") {
    SIDE_PANEL_DRAWER_MEDIA.addListener(syncResponsivePanels);
  }
  syncResponsivePanels();

  async function hydrateAuthFromServer() {
    try {
      const res = await fetch("/api/auth/me", { credentials: "include" });
      if (!res.ok) return;

      const data = await res.json();
      if (data && data.status === "success" && data.user && data.user.id) {
        // Merge server data with existing sessionStorage (server is authoritative)
        const existing = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
        const merged = { ...existing, ...data.user };
        sessionStorage.setItem("kway_user", JSON.stringify(merged));
        localStorage.setItem("kway_chat_session", data.user.id);
        state.sessionId = data.user.id;

        // ── Update closure-scoped userData so NEW message bubbles use the real picture ──
        if (userData) {
          userData.name = merged.name || userData.name;
          userData.picture = merged.picture || userData.picture || "";
          userData.initials = merged.initials || (merged.name ? merged.name.slice(0, 2).toUpperCase() : userData.initials);
          userData.dept = merged.department || merged.department_name || userData.dept || "";
          userData.email = merged.email || userData.email || "";
        }

        const pic = merged.picture || "";
        const displayName = merged.name || "LINE User";
        const displayInitials = merged.initials || displayName.slice(0, 2).toUpperCase();
        const deptLine = merged.department || merged.department_name
          ? `${merged.department || merged.department_name}${merged.title ? " · " + merged.title : ""}`
          : "已登入";

        // ── Topbar avatar (top-right circle) ──
        const topbarAvatar = document.getElementById("topbarAvatar");
        if (topbarAvatar) {
          if (pic) {
            topbarAvatar.style.backgroundImage = `url(${pic})`;
            topbarAvatar.style.backgroundSize = "cover";
            topbarAvatar.style.backgroundPosition = "center";
            topbarAvatar.textContent = "";
          } else {
            topbarAvatar.textContent = displayInitials;
          }
        }

        // ── Right sidebar user card (工作面板 → 資訊) ──
        const sidebarAvatar = document.getElementById("sidebarAvatar");
        if (sidebarAvatar) {
          if (pic) {
            sidebarAvatar.style.backgroundImage = `url(${pic})`;
            sidebarAvatar.style.backgroundSize = "cover";
            sidebarAvatar.style.backgroundPosition = "center";
            sidebarAvatar.style.background = `center/cover no-repeat url(${pic})`;
            sidebarAvatar.textContent = "";
          } else {
            sidebarAvatar.textContent = displayInitials;
          }
        }
        const sidebarName = document.getElementById("sidebarName");
        if (sidebarName) sidebarName.textContent = displayName;
        const sidebarDept = document.getElementById("sidebarDept");
        if (sidebarDept) sidebarDept.textContent = deptLine;

        // ── Admin panel button — LINE login hydrates role asynchronously, so
        // the inline visibility check in chat.html runs before role is known.
        // Re-evaluate here now that we have the authoritative user data.
        const adminBtn = document.getElementById("btnAdminPanel");
        if (adminBtn) {
          adminBtn.style.display = merged.role === "admin" ? "" : "none";
        }

        // ── First-login identity verification prompt ──
        // If user hasn't completed onboarding (no employee_id bound), show a
        // persistent banner linking to settings.html where the full verify
        // modal lives. Non-blocking — user can still chat as guest.
        const notVerified = !merged.onboarding_completed || !merged.employee_id;
        if (notVerified) {
          _showIdVerifyBanner();
        } else {
          _hideIdVerifyBanner();
        }

        // ── Refresh avatars on already-rendered user messages ──
        document.querySelectorAll(".page-chat-msg-row--user .avatar.avatar-sm").forEach(el => {
          if (pic) {
            el.innerHTML = `<img src="${pic}" referrerpolicy="no-referrer" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" onerror="this.parentElement.textContent='${displayInitials.replace(/'/g, "\\'")}'">`;
          } else {
            el.textContent = displayInitials;
          }
        });
      }
    } catch (_err) {
      // best effort
    }
  }

  /* ── First-login verification banner ────────────────────────────────── */
  function _showIdVerifyBanner() {
    if (document.getElementById("idVerifyBanner")) return;  // already shown
    // Respect user's temporary dismissal (session-level)
    if (sessionStorage.getItem("kway_id_banner_dismissed") === "1") return;

    const banner = document.createElement("div");
    banner.id = "idVerifyBanner";
    banner.style.cssText = [
      "position:fixed",
      "top:62px",
      "left:50%",
      "transform:translateX(-50%)",
      "background:#fff8e1",
      "color:#78491a",
      "border:1.5px solid #ffd980",
      "border-radius:10px",
      "padding:10px 16px",
      "box-shadow:0 6px 18px rgba(0,0,0,0.10)",
      "z-index:500",
      "font-size:0.82rem",
      "display:flex",
      "align-items:center",
      "gap:12px",
      "max-width:92vw",
    ].join(";");
    banner.innerHTML =
      '<span style="font-size:1.1rem;">⚠️</span>' +
      '<span>首次登入尚未完成身分驗證，綁定員工資料後可使用個人化功能</span>' +
      '<a href="settings.html?verify=1" style="color:#4a90d9;font-weight:700;text-decoration:none;padding:3px 10px;border-radius:6px;border:1px solid #4a90d9;">前往驗證</a>' +
      '<button onclick="_dismissIdVerifyBanner()" style="background:none;border:none;color:#888;cursor:pointer;font-size:1.1rem;line-height:1;padding:0 2px;" title="稍後再提醒">✕</button>';
    document.body.appendChild(banner);
  }

  function _hideIdVerifyBanner() {
    document.getElementById("idVerifyBanner")?.remove();
  }

  window._dismissIdVerifyBanner = function () {
    sessionStorage.setItem("kway_id_banner_dismissed", "1");
    _hideIdVerifyBanner();
  };

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

  function formatFileSize(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "0 KB";
    if (value < 1024 * 1024) return Math.max(1, Math.round(value / 1024)) + " KB";
    return (value / (1024 * 1024)).toFixed(1) + " MB";
  }

  function getUserDocumentStatusMeta(status) {
    switch (String(status || "").toLowerCase()) {
      case "ready":
        return { label: "可讀取", className: "" };
      case "pending":
        return { label: "整理中", className: " is-pending" };
      case "failed":
        return { label: "抽取失敗", className: " is-failed" };
      default:
        return { label: "可預覽", className: "" };
    }
  }

  function formatUserDocumentExpiry(expiresAt) {
    if (!expiresAt) return "";
    const target = new Date(expiresAt);
    if (Number.isNaN(target.getTime())) return "";
    const diffMs = target.getTime() - Date.now();
    const diffDays = Math.ceil(diffMs / 86400000);
    if (diffDays <= 0) return "今天到期";
    if (diffDays === 1) return "1 天後到期";
    return diffDays + " 天後到期";
  }

  function formatAudioClock(seconds) {
    const sec = Math.max(0, Math.floor(Number(seconds || 0)));
    const hours = Math.floor(sec / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    const remain = sec % 60;
    if (hours > 0) {
      return String(hours) + ":" + String(minutes).padStart(2, "0") + ":" + String(remain).padStart(2, "0");
    }
    return String(minutes).padStart(2, "0") + ":" + String(remain).padStart(2, "0");
  }

  function formatDocumentTimestamp(value) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "";
    try {
      return parsed.toLocaleString("zh-TW", { hour12: false });
    } catch (_err) {
      return parsed.toISOString().replace("T", " ").slice(0, 19);
    }
  }

  function buildAudioMetaPill(label, value) {
    if (!value) return null;
    const pill = document.createElement("div");
    pill.className = "page-chat-audio-preview-pill";

    const labelEl = document.createElement("span");
    labelEl.className = "page-chat-audio-preview-pill-label";
    labelEl.textContent = label;

    const valueEl = document.createElement("strong");
    valueEl.className = "page-chat-audio-preview-pill-value";
    valueEl.textContent = value;

    pill.appendChild(labelEl);
    pill.appendChild(valueEl);
    return pill;
  }

  function createAudioPreviewPanel(options) {
    const panel = document.createElement("section");
    panel.className = "page-chat-audio-preview";
    panel.tabIndex = 0;

    const src = String(options.audioSrc || "");
    const meta = options.audioMeta || {};

    const summary = document.createElement("div");
    summary.className = "page-chat-audio-preview-summary";
    [
      buildAudioMetaPill("格式", ((meta.extension || "").replace(".", "").toUpperCase()) || "AUDIO"),
      buildAudioMetaPill("大小", meta.size ? formatFileSize(meta.size) : ""),
      buildAudioMetaPill("MIME", meta.mimeType || ""),
      buildAudioMetaPill("建立時間", formatDocumentTimestamp(meta.createdAt)),
    ].forEach(function (pill) {
      if (pill) summary.appendChild(pill);
    });

    const waveform = document.createElement("div");
    waveform.className = "page-chat-audio-preview-waveform";
    waveform.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 36; i += 1) {
      const bar = document.createElement("span");
      bar.className = "page-chat-audio-preview-wave-bar";
      bar.style.animationDelay = String((i % 9) * 0.08) + "s";
      bar.style.height = String(18 + ((i * 7) % 42)) + "px";
      waveform.appendChild(bar);
    }

    const audio = document.createElement("audio");
    audio.className = "page-chat-audio-preview-native";
    audio.preload = "metadata";
    audio.src = src;
    audio.setAttribute("playsinline", "playsinline");

    const controlCard = document.createElement("div");
    controlCard.className = "page-chat-audio-preview-controls";

    const timelineHead = document.createElement("div");
    timelineHead.className = "page-chat-audio-preview-timeline-head";
    const currentTimeEl = document.createElement("span");
    currentTimeEl.textContent = "00:00";
    const durationEl = document.createElement("span");
    durationEl.textContent = "--:--";
    timelineHead.appendChild(currentTimeEl);
    timelineHead.appendChild(durationEl);

    const timelineRange = document.createElement("input");
    timelineRange.className = "page-chat-audio-preview-timeline";
    timelineRange.type = "range";
    timelineRange.min = "0";
    timelineRange.max = "1000";
    timelineRange.value = "0";
    timelineRange.disabled = true;
    timelineRange.setAttribute("aria-label", "音訊播放進度");

    const row = document.createElement("div");
    row.className = "page-chat-audio-preview-control-row";

    const transport = document.createElement("div");
    transport.className = "page-chat-audio-preview-transport";

    const backBtn = document.createElement("button");
    backBtn.type = "button";
    backBtn.className = "page-chat-audio-preview-btn";
    backBtn.textContent = "⟲ 10 秒";

    const playBtn = document.createElement("button");
    playBtn.type = "button";
    playBtn.className = "page-chat-audio-preview-btn is-primary";
    playBtn.textContent = "播放";

    const forwardBtn = document.createElement("button");
    forwardBtn.type = "button";
    forwardBtn.className = "page-chat-audio-preview-btn";
    forwardBtn.textContent = "10 秒 ⟳";

    transport.appendChild(backBtn);
    transport.appendChild(playBtn);
    transport.appendChild(forwardBtn);

    const settings = document.createElement("div");
    settings.className = "page-chat-audio-preview-settings";

    const speedWrap = document.createElement("label");
    speedWrap.className = "page-chat-audio-preview-field";
    speedWrap.textContent = "速度";

    const speedSelect = document.createElement("select");
    speedSelect.className = "page-chat-audio-preview-select";
    [0.75, 1, 1.25, 1.5, 2].forEach(function (rate) {
      const opt = document.createElement("option");
      opt.value = String(rate);
      opt.textContent = String(rate) + "x";
      if (rate === 1) opt.selected = true;
      speedSelect.appendChild(opt);
    });
    speedWrap.appendChild(speedSelect);

    const volumeWrap = document.createElement("label");
    volumeWrap.className = "page-chat-audio-preview-field";
    volumeWrap.textContent = "音量";

    const volumeRange = document.createElement("input");
    volumeRange.className = "page-chat-audio-preview-volume";
    volumeRange.type = "range";
    volumeRange.min = "0";
    volumeRange.max = "100";
    volumeRange.step = "1";
    volumeRange.value = "100";
    volumeRange.setAttribute("aria-label", "音量");
    volumeWrap.appendChild(volumeRange);

    settings.appendChild(speedWrap);
    settings.appendChild(volumeWrap);

    row.appendChild(transport);
    row.appendChild(settings);

    const hint = document.createElement("div");
    hint.className = "page-chat-audio-preview-hint";
    hint.textContent = "支援快捷鍵：空白鍵播放/暫停，← / → 快退或快進 10 秒。";

    const error = document.createElement("div");
    error.className = "page-chat-audio-preview-error";
    error.style.display = "none";

    controlCard.appendChild(timelineHead);
    controlCard.appendChild(timelineRange);
    controlCard.appendChild(row);
    controlCard.appendChild(hint);
    controlCard.appendChild(error);

    panel.appendChild(summary);
    panel.appendChild(waveform);
    panel.appendChild(controlCard);
    panel.appendChild(audio);

    if (!src) {
      error.style.display = "";
      error.textContent = "音訊來源遺失，請改用「下載原檔」後再試。";
      return panel;
    }

    let isSeeking = false;

    function syncPlayButton() {
      playBtn.textContent = audio.paused ? (audio.ended ? "重播" : "播放") : "暫停";
    }

    function syncTimeline(fromSeekInput) {
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
      const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
      currentTimeEl.textContent = formatAudioClock(current);
      durationEl.textContent = duration > 0 ? formatAudioClock(duration) : "--:--";
      timelineRange.disabled = duration <= 0;
      if (!isSeeking || fromSeekInput) {
        timelineRange.value = duration > 0 ? String(Math.round((current / duration) * 1000)) : "0";
      }
      syncPlayButton();
    }

    function seekBy(seconds) {
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
      if (!duration) return;
      const next = Math.min(Math.max(0, audio.currentTime + seconds), duration);
      audio.currentTime = next;
      syncTimeline(true);
    }

    playBtn.addEventListener("click", function () {
      if (audio.paused) {
        if (audio.ended) audio.currentTime = 0;
        audio.play().catch(function () {
          error.style.display = "";
          error.textContent = "播放失敗，請確認瀏覽器已允許音訊播放。";
        });
      } else {
        audio.pause();
      }
      syncPlayButton();
    });

    backBtn.addEventListener("click", function () {
      seekBy(-10);
    });

    forwardBtn.addEventListener("click", function () {
      seekBy(10);
    });

    timelineRange.addEventListener("input", function () {
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
      if (!duration) return;
      isSeeking = true;
      const ratio = Number(timelineRange.value) / 1000;
      currentTimeEl.textContent = formatAudioClock(duration * ratio);
    });

    timelineRange.addEventListener("change", function () {
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
      if (!duration) return;
      const ratio = Number(timelineRange.value) / 1000;
      audio.currentTime = duration * ratio;
      isSeeking = false;
      syncTimeline(true);
    });

    timelineRange.addEventListener("pointerup", function () {
      isSeeking = false;
    });

    speedSelect.addEventListener("change", function () {
      const rate = Number(speedSelect.value);
      audio.playbackRate = Number.isFinite(rate) && rate > 0 ? rate : 1;
    });

    volumeRange.addEventListener("input", function () {
      const level = Number(volumeRange.value);
      audio.volume = Number.isFinite(level) ? Math.min(Math.max(level / 100, 0), 1) : 1;
    });

    panel.addEventListener("keydown", function (event) {
      if (event.target && (event.target.tagName === "INPUT" || event.target.tagName === "SELECT")) return;
      if (event.code === "Space") {
        event.preventDefault();
        playBtn.click();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        seekBy(-10);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        seekBy(10);
      }
    });

    audio.addEventListener("loadedmetadata", function () {
      error.style.display = "none";
      syncTimeline(false);
    });
    audio.addEventListener("timeupdate", function () {
      syncTimeline(false);
    });
    audio.addEventListener("play", syncPlayButton);
    audio.addEventListener("pause", syncPlayButton);
    audio.addEventListener("ended", syncPlayButton);
    audio.addEventListener("error", function () {
      error.style.display = "";
      error.textContent = "無法載入這個音訊檔，請改用「下載原檔」檢查檔案。";
    });

    syncTimeline(false);
    return panel;
  }

  function syncDocumentCenterVisibility(activeTab) {
    document.querySelectorAll(".page-chat-doc-center-panel").forEach(function (el) {
      el.style.display = activeTab === "docs" ? "" : "none";
    });
  }

  let userDocModalPseudoFullscreen = false;

  function isUserDocumentModalBrowserFullscreen(modal) {
    return !!modal && (
      document.fullscreenElement === modal ||
      document.webkitFullscreenElement === modal
    );
  }

  function syncUserDocumentModalFullscreenUi() {
    const modal = document.getElementById("userDocModal");
    const fullscreenBtn = document.getElementById("userDocModalFullscreenBtn");
    if (!modal) return;

    const isFullscreen = userDocModalPseudoFullscreen || isUserDocumentModalBrowserFullscreen(modal);
    modal.classList.toggle("is-fullscreen", isFullscreen);

    if (fullscreenBtn) {
      fullscreenBtn.textContent = isFullscreen ? "退出全螢幕" : "全螢幕";
      fullscreenBtn.setAttribute("aria-pressed", isFullscreen ? "true" : "false");
    }
  }

  async function exitUserDocumentModalFullscreen() {
    const modal = document.getElementById("userDocModal");
    if (!modal) return;

    userDocModalPseudoFullscreen = false;
    if (isUserDocumentModalBrowserFullscreen(modal)) {
      try {
        if (typeof document.exitFullscreen === "function") {
          await document.exitFullscreen();
        } else if (typeof document.webkitExitFullscreen === "function") {
          document.webkitExitFullscreen();
        }
      } catch (_err) {
        // Ignore and let the modal fall back to normal size.
      }
    }

    syncUserDocumentModalFullscreenUi();
  }

  async function toggleUserDocumentModalFullscreen() {
    const modal = document.getElementById("userDocModal");
    if (!modal) return;

    if (userDocModalPseudoFullscreen || isUserDocumentModalBrowserFullscreen(modal)) {
      await exitUserDocumentModalFullscreen();
      return;
    }

    try {
      if (typeof modal.requestFullscreen === "function") {
        await modal.requestFullscreen();
      } else if (typeof modal.webkitRequestFullscreen === "function") {
        modal.webkitRequestFullscreen();
      } else {
        userDocModalPseudoFullscreen = true;
      }
    } catch (_err) {
      userDocModalPseudoFullscreen = true;
    }

    syncUserDocumentModalFullscreenUi();
  }

  function openUserDocumentModal(options) {
    const modal = document.getElementById("userDocModal");
    const title = document.getElementById("userDocModalTitle");
    const subtitle = document.getElementById("userDocModalSubtitle");
    const body = document.getElementById("userDocModalBody");
    const link = document.getElementById("userDocModalLink");
    const expandBtn = document.getElementById("userDocModalExpandBtn");
    const fullscreenBtn = document.getElementById("userDocModalFullscreenBtn");
    if (!modal || !title || !subtitle || !body || !link || !expandBtn || !fullscreenBtn) return;

    title.textContent = options.title || "文件預覽";
    subtitle.textContent = options.subtitle || "";
    body.innerHTML = "";

    if (options.mode === "loading") {
      const loading = document.createElement("div");
      loading.className = "page-chat-doc-modal-loading";
      loading.textContent = options.loadingText || "正在讀取文件...";
      body.appendChild(loading);
    } else if (options.mode === "audio") {
      body.appendChild(createAudioPreviewPanel(options));
    } else if (options.mode === "iframe") {
      const iframe = document.createElement("iframe");
      iframe.className = "page-chat-doc-modal-frame";
      iframe.src = options.src || "";
      iframe.title = options.title || "文件預覽";
      body.appendChild(iframe);
    } else {
      const pre = document.createElement("pre");
      pre.className = "page-chat-doc-modal-text";
      pre.textContent = options.text || "沒有可顯示的內容";
      body.appendChild(pre);
    }

    if (options.linkHref) {
      link.style.display = "inline-flex";
      link.href = options.linkHref;
      link.textContent = options.linkLabel || "下載原檔";
    } else {
      link.style.display = "none";
      link.removeAttribute("href");
    }

    if (typeof options.onExpand === "function") {
      expandBtn.style.display = "inline-flex";
      expandBtn.textContent = options.expandLabel || "載入全文";
      expandBtn.onclick = options.onExpand;
    } else {
      expandBtn.style.display = "none";
      expandBtn.onclick = null;
    }

    fullscreenBtn.style.display = options.mode === "iframe" ? "inline-flex" : "none";
    userDocModalPseudoFullscreen = false;
    syncUserDocumentModalFullscreenUi();
    modal.style.display = "flex";
  }

  function closeUserDocumentModal() {
    const modal = document.getElementById("userDocModal");
    const body = document.getElementById("userDocModalBody");
    const expandBtn = document.getElementById("userDocModalExpandBtn");
    const fullscreenBtn = document.getElementById("userDocModalFullscreenBtn");
    if (body) {
      body.querySelectorAll("audio").forEach(function (node) {
        try {
          node.pause();
          node.currentTime = 0;
        } catch (_err) {
          // ignore pause errors
        }
      });
      body.innerHTML = "";
    }
    if (expandBtn) expandBtn.onclick = null;
    if (fullscreenBtn) fullscreenBtn.style.display = "none";
    exitUserDocumentModalFullscreen();
    if (modal) modal.style.display = "none";
  }
  window.closeUserDocumentModal = closeUserDocumentModal;

  function handleDocumentAction(action, task) {
    if (!action || !task) return;
    if (action.type === "open_preview" && action.doc_id) {
      if (task.sessionId === state.sessionId) {
        setTimeout(function () {
          openUserDocumentPreview(action.doc_id);
        }, 0);
      } else if (!task.documentToastShown) {
        task.documentToastShown = true;
        showToast("「" + (action.display_name || "文件") + "」已可預覽", "info");
      }
    }
  }

  function updateUserDocumentStats() {
    const count = Array.isArray(state.userDocuments) ? state.userDocuments.length : 0;
    const countEl = document.getElementById("userDocumentCount");
    const statEl = document.getElementById("statUserDocCount");
    if (countEl) countEl.textContent = String(count);
    if (statEl) statEl.textContent = String(count);
  }

  function renderUserDocumentEmpty(message) {
    const empty = document.getElementById("userDocumentEmpty");
    const list = document.getElementById("userDocumentList");
    if (!empty || !list) return;
    empty.textContent = message || "尚未上傳文件";
    empty.style.display = "";
    list.innerHTML = "";
    updateUserDocumentStats();
  }

  function isAudioUserDocument(doc) {
    if (!doc) return false;
    if (doc.preview_type === "audio-inline") return true;
    const ext = String(doc.extension || "").toLowerCase();
    return [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".flac"].indexOf(ext) !== -1;
  }

  function buildUserDocumentActionPrompt(doc, action) {
    const name = doc && (doc.display_name || doc.original_filename || doc.doc_id) || "這份檔案";
    if (action === "meeting_notes") {
      return "請使用我在文件中心指定的檔案「" + name + "」整理會議紀錄，包含重點、決策、風險與後續行動。";
    }
    if (action === "transcript") {
      return "請使用我在文件中心指定的檔案「" + name + "」先產出逐字稿，盡量保留說話者分段。";
    }
    if (action === "todo") {
      return "請使用我在文件中心指定的檔案「" + name + "」，整理出可執行的 Todo 清單與優先順序。";
    }
    return "請使用我在文件中心指定的檔案「" + name + "」協助處理。";
  }

  function triggerUserDocumentAction(doc, action) {
    if (!doc || !doc.doc_id) return;
    const normalizedAction = String(action || "").trim();
    if (!normalizedAction) return;
    if (listActiveTasksForSession(state.sessionId).length > 0) {
      showToast("目前仍有任務執行中，請稍候再試", "info");
      return;
    }
    const prompt = buildUserDocumentActionPrompt(doc, normalizedAction);
    showToast("已使用「" + (doc.display_name || doc.original_filename || "文件") + "」啟動任務", "info");
    sendMessage(prompt, {
      userDocumentId: doc.doc_id,
      userDocumentAction: normalizedAction,
    });
  }

  function renderUserDocuments(documents) {
    const list = document.getElementById("userDocumentList");
    const empty = document.getElementById("userDocumentEmpty");
    if (!list || !empty) return;

    state.userDocuments = Array.isArray(documents) ? documents.slice() : [];
    updateUserDocumentStats();
    list.innerHTML = "";

    if (!state.userDocuments.length) {
      empty.textContent = "尚未上傳文件";
      empty.style.display = "";
      return;
    }

    empty.style.display = "none";

    state.userDocuments.forEach(function (doc) {
      const item = document.createElement("div");
      item.className = "page-chat-doc-item";

      const head = document.createElement("div");
      head.className = "page-chat-doc-item-head";

      const info = document.createElement("div");
      const name = document.createElement("div");
      name.className = "page-chat-doc-item-name";
      name.textContent = doc.display_name || doc.original_filename || doc.stored_filename || doc.doc_id;

      const meta = document.createElement("div");
      meta.className = "page-chat-doc-item-meta";
      const metaParts = [
        (doc.extension || "").replace(".", "").toUpperCase() || "FILE",
        formatFileSize(doc.size),
      ];
      const expiryLabel = formatUserDocumentExpiry(doc.expires_at);
      if (expiryLabel) metaParts.push(expiryLabel);
      meta.textContent = metaParts.join(" · ");

      info.appendChild(name);
      info.appendChild(meta);

      const statusMeta = getUserDocumentStatusMeta(doc.text_extract_status);
      const status = document.createElement("span");
      status.className = "page-chat-doc-status" + statusMeta.className;
      status.textContent = statusMeta.label;

      head.appendChild(info);
      head.appendChild(status);

      const actions = document.createElement("div");
      actions.className = "page-chat-doc-item-actions";

      const previewBtn = document.createElement("button");
      previewBtn.type = "button";
      previewBtn.className = "page-chat-doc-action-btn";
      previewBtn.textContent = "預覽";
      previewBtn.addEventListener("click", function () {
        openUserDocumentPreview(doc.doc_id);
      });

      const textBtn = document.createElement("button");
      textBtn.type = "button";
      textBtn.className = "page-chat-doc-action-btn";
      if (isAudioUserDocument(doc)) {
        textBtn.textContent = "原檔";
        textBtn.addEventListener("click", function () {
          window.open(
            "/api/user-documents/" + encodeURIComponent(doc.doc_id) + "/file?disposition=inline",
            "_blank",
            "noopener"
          );
        });
      } else {
        textBtn.textContent = "文字";
        textBtn.addEventListener("click", function () {
          openUserDocumentText(doc.doc_id, doc.display_name || doc.original_filename || "文件");
        });
      }

      const renameBtn = document.createElement("button");
      renameBtn.type = "button";
      renameBtn.className = "page-chat-doc-action-btn";
      renameBtn.textContent = "改名";
      renameBtn.addEventListener("click", function () {
        renameUserDocument(doc.doc_id, doc.display_name || doc.original_filename || "");
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "page-chat-doc-action-btn is-danger";
      deleteBtn.textContent = "刪除";
      deleteBtn.addEventListener("click", function () {
        deleteUserDocument(doc.doc_id, doc.display_name || doc.original_filename || "文件");
      });

      actions.appendChild(previewBtn);
      actions.appendChild(textBtn);

      if (isAudioUserDocument(doc)) {
        const meetingBtn = document.createElement("button");
        meetingBtn.type = "button";
        meetingBtn.className = "page-chat-doc-action-btn";
        meetingBtn.textContent = "會議紀錄";
        meetingBtn.addEventListener("click", function () {
          triggerUserDocumentAction(doc, "meeting_notes");
        });

        const transcriptBtn = document.createElement("button");
        transcriptBtn.type = "button";
        transcriptBtn.className = "page-chat-doc-action-btn";
        transcriptBtn.textContent = "逐字稿";
        transcriptBtn.addEventListener("click", function () {
          triggerUserDocumentAction(doc, "transcript");
        });

        const todoBtn = document.createElement("button");
        todoBtn.type = "button";
        todoBtn.className = "page-chat-doc-action-btn";
        todoBtn.textContent = "Todo";
        todoBtn.addEventListener("click", function () {
          triggerUserDocumentAction(doc, "todo");
        });

        actions.appendChild(meetingBtn);
        actions.appendChild(transcriptBtn);
        actions.appendChild(todoBtn);
      }

      actions.appendChild(renameBtn);
      actions.appendChild(deleteBtn);

      item.appendChild(head);
      item.appendChild(actions);
      list.appendChild(item);
    });
  }

  async function loadUserDocuments(options) {
    const opts = options || {};
    if (!opts.silent) {
      renderUserDocumentEmpty("正在讀取文件列表...");
    }
    try {
      const res = await fetch("/api/user-documents", { credentials: "same-origin" });
      if (res.status === 401) {
        state.userDocuments = [];
        renderUserDocumentEmpty("登入後即可使用文件中心");
        return;
      }
      const data = await res.json();
      if (!res.ok || data.status !== "success") {
        throw new Error(data.detail || "Load failed");
      }
      renderUserDocuments(data.documents || []);
      if (opts.withToast) showToast("已刷新文件列表", "success");
    } catch (err) {
      state.userDocuments = [];
      renderUserDocumentEmpty("文件列表載入失敗");
      if (!opts.silent) showToast("文件列表載入失敗：" + (err.message || "未知錯誤"), "error");
    }
  }

  async function openUserDocumentPreview(docId) {
    openUserDocumentModal({
      title: "文件預覽",
      subtitle: "正在準備內容...",
      mode: "loading",
      loadingText: "正在讀取文件...",
    });
    try {
      const res = await fetch("/api/user-documents/" + encodeURIComponent(docId) + "/preview", {
        credentials: "same-origin",
      });
      const data = await res.json();
      if (!res.ok || data.status !== "success") {
        throw new Error(data.detail || "Preview failed");
      }

      const displayName = (data.document && (data.document.display_name || data.document.original_filename)) || "文件";
      if (data.preview_type === "audio-inline") {
        openUserDocumentModal({
          title: displayName,
          subtitle: "服務內音訊預覽",
          mode: "audio",
          audioSrc: data.inline_url || "",
          audioMeta: {
            extension: data.document && data.document.extension,
            mimeType: data.document && data.document.mime_type,
            size: data.document && data.document.size,
            createdAt: data.document && data.document.created_at,
          },
          linkHref: data.viewer_url || data.download_url || data.inline_url,
          linkLabel: data.viewer_url ? "完整預覽頁" : "下載原檔",
        });
        return;
      }

      if (data.preview_type === "pdf-inline" || data.preview_type === "html-inline") {
        const htmlPreviewLink = data.viewer_url || data.download_url;
        const pdfPreviewLink = data.preview_type === "html-inline" ? (data.pdf_viewer_url || data.pdf_inline_url) : null;
        openUserDocumentModal({
          title: displayName,
          subtitle:
            data.preview_type === "pdf-inline"
              ? "服務內 PDF 預覽"
              : "服務內 DOCX HTML 預覽",
          mode: "iframe",
          src: data.inline_url || data.viewer_url,
          linkHref: pdfPreviewLink || htmlPreviewLink,
          linkLabel: pdfPreviewLink ? "PDF 預覽" : "完整預覽",
          onExpand: data.preview_type === "html-inline" && htmlPreviewLink
            ? function () {
                window.open(htmlPreviewLink, "_blank", "noopener");
              }
            : null,
          expandLabel: data.preview_type === "html-inline" && htmlPreviewLink ? "新分頁開啟" : undefined,
        });
        return;
      }

      openUserDocumentModal({
        title: displayName,
        subtitle: data.truncated ? "目前顯示預覽片段" : "目前顯示文件文字內容",
        mode: "text",
        text: data.text_preview || "",
        linkHref: data.viewer_url || data.download_url,
        linkLabel: data.viewer_url ? "完整預覽" : "下載原檔",
        onExpand: data.truncated
          ? function () {
              openUserDocumentText(docId, displayName);
            }
          : null,
      });
    } catch (err) {
      openUserDocumentModal({
        title: "文件預覽",
        subtitle: "",
        mode: "text",
        text: "文件預覽失敗：" + (err.message || "未知錯誤"),
      });
    }
  }

  async function openUserDocumentText(docId, displayName) {
    openUserDocumentModal({
      title: displayName || "文件文字內容",
      subtitle: "正在讀取全文...",
      mode: "loading",
      loadingText: "正在載入全文...",
    });
    try {
      const res = await fetch(
        "/api/user-documents/" + encodeURIComponent(docId) + "/content?offset=0&limit=200000",
        { credentials: "same-origin" }
      );
      const data = await res.json();
      if (!res.ok || data.status !== "success") {
        throw new Error(data.detail || "Content failed");
      }
      openUserDocumentModal({
        title: displayName || ((data.document && data.document.display_name) || "文件文字內容"),
        subtitle: data.truncated ? "已載入首段內容，完整內容可改用預覽頁查看" : "已載入完整文字",
        mode: "text",
        text: (data.content || "") + (data.truncated ? "\n\n[內容仍然很長，建議改用完整預覽頁閱讀。]" : ""),
        linkHref: "/api/user-documents/" + encodeURIComponent(docId) + "/file?disposition=attachment",
        linkLabel: "下載原檔",
        onExpand: data.truncated
          ? function () {
              window.open("/api/user-documents/" + encodeURIComponent(docId) + "/viewer", "_blank", "noopener");
            }
          : null,
        expandLabel: "完整預覽",
      });
    } catch (err) {
      openUserDocumentModal({
        title: displayName || "文件文字內容",
        subtitle: "",
        mode: "text",
        text: "文字內容載入失敗：" + (err.message || "未知錯誤"),
      });
    }
  }

  async function renameUserDocument(docId, currentName) {
    const nextName = window.prompt("新的文件名稱", currentName || "");
    if (!nextName || nextName === currentName) return;
    try {
      const res = await fetch("/api/user-documents/" + encodeURIComponent(docId) + "/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ display_name: nextName }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "success") {
        throw new Error(data.detail || "Rename failed");
      }
      showToast("已更新文件名稱", "success");
      loadUserDocuments({ silent: true });
    } catch (err) {
      showToast("文件改名失敗：" + (err.message || "未知錯誤"), "error");
    }
  }

  async function deleteUserDocument(docId, currentName) {
    if (!window.confirm("確定要刪除「" + (currentName || "文件") + "」嗎？")) return;
    try {
      const res = await fetch("/api/user-documents/" + encodeURIComponent(docId), {
        method: "DELETE",
        credentials: "same-origin",
      });
      const data = await res.json();
      if (!res.ok || data.status !== "success") {
        throw new Error(data.detail || "Delete failed");
      }
      showToast("已刪除文件", "success");
      loadUserDocuments({ silent: true });
      closeUserDocumentModal();
    } catch (err) {
      showToast("文件刪除失敗：" + (err.message || "未知錯誤"), "error");
    }
  }

  function triggerUserDocumentUpload() {
    const input = document.getElementById("userDocUploadInput");
    if (!input) return;
    input.value = "";
    input.onchange = async function () {
      const file = input.files && input.files[0];
      if (!file) return;
      const MAX_BYTES = 25 * 1024 * 1024;
      if (file.size > MAX_BYTES) {
        showToast("文件過大，請控制在 25 MB 內", "error");
        return;
      }

      showToast("正在上傳「" + file.name + "」...", "info");
      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("/api/user-documents/upload", {
          method: "POST",
          credentials: "same-origin",
          body: formData,
        });
        const data = await res.json();
        if (!res.ok || data.status !== "success") {
          throw new Error(data.detail || "Upload failed");
        }
        showToast("已上傳「" + file.name + "」", "success");
        loadUserDocuments({ silent: true });
      } catch (err) {
        showToast("文件上傳失敗：" + (err.message || "未知錯誤"), "error");
      }
    };
    input.click();
  }

  window.refreshUserDocuments = function () {
    loadUserDocuments({ silent: false, withToast: true });
  };
  window.triggerUserDocumentUpload = triggerUserDocumentUpload;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function formatText(text) {
    return escapeHtml(text)
      .replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|\/)[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>")
      ;
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
    moveSessionScopedValue(state.sessionPendingAudioFile, sessionId, realSessionId, null);
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

  function isLocalAudioHandoffPrompt(content) {
    if (typeof content !== "string") return false;
    return (
      content.indexOf("我已收到音檔「") === 0 &&
      content.indexOf("你希望我接下來做什麼？例如：") !== -1 &&
      content.indexOf("1. 轉逐字稿") !== -1 &&
      content.indexOf("3. 產出 Todo 並上傳 Notion") !== -1
    );
  }

  function getCachedHistory(sessionId) {
    const cached = state.sessionHistoryCache[sessionId];
    if (!Array.isArray(cached)) return [];
    return cached
      .map(cloneHistoryMessage)
      .filter(Boolean)
      .filter((msg) => !(msg.role === "assistant" && isLocalAudioHandoffPrompt(msg.content)));
  }

  function setCachedHistory(sessionId, history) {
    state.sessionHistoryCache[sessionId] = (Array.isArray(history) ? history : [])
      .map(cloneHistoryMessage)
      .filter(Boolean);
  }

  function pruneLocalAudioHandoffPromptFromCache(sessionId) {
    const current = getCachedHistory(sessionId);
    if (!current.length) return;
    const filtered = current.filter((msg) => {
      if (!msg || msg.role !== "assistant") return true;
      return !isLocalAudioHandoffPrompt(msg.content);
    });
    if (filtered.length !== current.length) {
      setCachedHistory(sessionId, filtered);
    }
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

    // Cleanup local-only handoff prompts from previous frontend versions.
    pruneLocalAudioHandoffPromptFromCache(sessionId);
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

  function getSessionDomSafeId(sessionId) {
    return String(sessionId || "default").replace(/[^a-zA-Z0-9_-]/g, "_");
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
    delete state.sessionPendingAudioFile[sessionId];
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
      '<div class="page-chat-welcome" id="chatWelcome-' + getSessionDomSafeId(sessionId) + '">' +
      '<div class="page-chat-welcome-logo"><img src="../assets/images/kw_logo.png" width="56" alt="Logo"></div>' +
      '<h2>' + escapeHtml(title) + '</h2>' +
      '<p>' + escapeHtml(body) + '</p>' +
      '</div>';
    state.sessionMsgCounts[sessionId] = 0;
    state.sessionTokenCounts[sessionId] = 0;
    state.sessionMeetingText[sessionId] = "";
  }

  function renderDraftWelcome(sessionId) {
    const container = getSessionContainer(sessionId);
    if (!container) return;

    if (!initialWelcomeMarkup) {
      renderConversationPlaceholder(sessionId, "開始新的對話", "輸入任何問題，或上傳音檔讓助手協助處理。");
      return;
    }

    container.innerHTML =
      '<div class="page-chat-welcome" id="chatWelcome-' + getSessionDomSafeId(sessionId) + '">' +
      initialWelcomeMarkup +
      "</div>";
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
    const hasActiveTaskInCurrentSession = listActiveTasksForSession(state.sessionId).length > 0;

    if (hasActiveTaskInCurrentSession) {
      // ── Stop mode: AI 正在回覆 ──
      sendBtn.classList.add("is-stop-mode");
      sendBtn.disabled = false;
      sendBtn.setAttribute("aria-label", "停止 AI 回覆");
      sendBtn.setAttribute("title", "停止 AI 回覆");
      sendBtn.dataset.mode = "stop";
      // Swap icon to a square stop icon
      sendBtn.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<rect x="6" y="6" width="12" height="12" rx="2" ry="2" fill="currentColor"/>' +
        '</svg>';
    } else {
      // ── Send mode (default) ──
      sendBtn.classList.remove("is-stop-mode");
      sendBtn.disabled = !hasText;
      sendBtn.setAttribute("aria-label", "送出訊息");
      sendBtn.setAttribute("title", "送出訊息");
      sendBtn.dataset.mode = "send";
      sendBtn.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<line x1="22" y1="2" x2="11" y2="13"></line>' +
        '<polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>' +
        '</svg>';
    }
    syncAudioRecorderButton();
  }

  // Cancel all active tasks for the current session (server + client side)
  async function stopAllActiveTasks() {
    const sessionId = state.sessionId;
    const active = listActiveTasksForSession(sessionId);
    if (!active.length) return;

    // 1. Tell server to cancel (non-blocking on error)
    try {
      await fetch(`/chat/stop_all/${encodeURIComponent(sessionId)}`, {
        method: "POST",
        credentials: "same-origin",
      });
    } catch (err) {
      console.warn("[stopAll] server call failed:", err);
    }

    // 2. Locally mark tasks as cancelled (server will also send SSE cancelled
    //    event, but we do it here immediately so UI responds instantly)
    active.forEach((task) => {
      task.status = "cancelled";
      task.completed = true;
      task.error = "已中止";
      // Close any open SSE reader for this task
      if (task.reader) {
        try { task.reader.cancel(); } catch (_) {}
      }
    });

    // 3. Clear typing, update bubble (append [已中止] marker if partial text)
    removeTyping(sessionId);
    active.forEach((task) => {
      if (task.text) {
        showTaskBubble(task, true);
      }
    });

    // 4. Re-enable input
    syncComposerState();
    renderConversationList();
    if (window.showToast) showToast("已中止 AI 回覆", "info");
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
      const res = await fetch("/chat/title-summary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_input: userInput,
          assistant_output: aiResponse,
          provider: model.provider || "openai",
          model: model.model || "gpt-4o",
          language: "繁體中文",
        }),
      });
      if (!res.ok) return;

      const data = await res.json();
      const cleanTitle = String((data && data.title) || "").replace(/['".!?]/g, "").trim().slice(0, 10);
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

    // Build avatar HTML — user: LINE picture or initials; AI: AgentK logo
    let avatarHtml;
    if (role === "user") {
      const pic = userData && typeof userData.picture === "string" && userData.picture.trim() ? userData.picture.trim() : "";
      if (pic) {
        avatarHtml = '<div class="avatar avatar-sm"><img src="' + escapeHtml(pic) + '" referrerpolicy="no-referrer" style="width:100%;height:100%;border-radius:50%;object-fit:cover;" onerror="this.parentElement.textContent=\'' + escapeHtml(initials) + '\'" /></div>';
      } else {
        avatarHtml = '<div class="avatar avatar-sm">' + escapeHtml(initials) + '</div>';
      }
    } else {
      avatarHtml = '<div class="avatar avatar-sm avatar-ai"><img src="../assets/images/kw_logo.png" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" /></div>';
    }

    row.innerHTML =
      avatarHtml +
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
      '<div class="avatar avatar-sm avatar-ai"><img src="../assets/images/kw_logo.png" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" /></div>' +
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
      const skillsRes = await fetch("/skills/list");

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
            task.toolMessage = parsed.message || task.toolMessage || "";
            task.riskDescription = parsed.risk_description || task.riskDescription || "";
            task.pendingArgs = parsed.pending_args || task.pendingArgs || {};
            task.firstChunkReceived = true;
            task.text =
              task.toolMessage ||
              ("等待授權以執行「" + (task.toolName || "tool") + "」。");
            removeTyping(task.sessionId);
            showTaskBubble(task, false);
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
            handleDocumentAction(parsed.document_action, task);
            return task.text;
          }
          if (parsed.status === "cancelled") {
            // Server confirms cancellation (either from our stop button or admin action)
            task.status = "cancelled";
            task.completed = true;
            task.text = (parsed.content || task.text || "") + (task.text ? "\n\n[已中止]" : "[已中止]");
            task.error = "";
            removeTyping(task.sessionId);
            showTaskBubble(task, true);
            syncComposerState();
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

  async function sendMessage(text, options) {
    const opts = options || {};
    const content = (text || "").trim();
    let requestSessionId = state.sessionId;
    const explicitUserDocumentId =
      typeof opts.userDocumentId === "string" ? opts.userDocumentId.trim() : "";
    const explicitUserDocumentAction =
      typeof opts.userDocumentAction === "string" ? opts.userDocumentAction.trim() : "";
    const shouldUseQueuedAttachment = !explicitUserDocumentId;
    const pendingAudio = shouldUseQueuedAttachment ? (state.sessionPendingAudioFile[requestSessionId] || null) : null;
    const explicitAttachedFile =
      typeof opts.attachedFile === "string" ? opts.attachedFile.trim() : "";
    // Generic attachment queued via 📎 button (takes precedence if neither of
    // explicit nor pending audio applies).
    const pendingAttachment = shouldUseQueuedAttachment && !explicitAttachedFile && !(pendingAudio && pendingAudio.path)
      ? _takeSessionAttachment(requestSessionId)
      : null;
    const attachedFileForTurn =
      explicitAttachedFile
      || (pendingAudio && pendingAudio.path ? pendingAudio.path : "")
      || (pendingAttachment && pendingAttachment.path ? pendingAttachment.path : "");
    const uploadHandoff = !!opts.uploadHandoff;
    const keepPendingAudio = !!opts.keepPendingAudio;
    // 只檢查當前 session 自己是否還在 pending;其他 session 的 task 完全不影響
    if (!content || listActiveTasksForSession(requestSessionId).length > 0) return;

    if (isDraftSessionId(requestSessionId)) {
      requestSessionId = materializeDraftSession(requestSessionId, content);
    }
    const turnId = generateTurnId();

    if (chatInput) {
      chatInput.value = "";
      state.sessionInputDrafts[requestSessionId] = "";
      autoResize(chatInput);
    }
    syncComposerState();

    ensureSessionExists(requestSessionId, "新對話", "開始新的對話...");
    pruneLocalAudioHandoffPromptFromCache(requestSessionId);
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
      if (attachedFileForTurn) {
        payload.attached_file = attachedFileForTurn;
      }
      if (explicitUserDocumentId) {
        payload.user_document_id = explicitUserDocumentId;
      }
      if (explicitUserDocumentAction) {
        payload.user_document_action = explicitUserDocumentAction;
      }
      if (uploadHandoff) {
        payload.upload_handoff = true;
      }

      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        removeTyping(requestSessionId);
        const errText = await res.text();
        let detail = errText;
        try {
          const parsed = JSON.parse(errText);
          detail = parsed.detail || parsed.message || errText;
        } catch (_err) {
          // keep raw text
        }
        throw new Error("HTTP " + res.status + ": " + detail);
      }

      const contentType = (res.headers.get("content-type") || "").toLowerCase();
      if (!contentType.includes("text/event-stream")) {
        removeTyping(requestSessionId);
        const rawText = await res.text();
        let detail = rawText || "Server did not return an event stream";
        try {
          const parsed = JSON.parse(rawText);
          detail = parsed.detail || parsed.message || rawText;
        } catch (_err) {
          // keep raw text
        }
        throw new Error(detail);
      }
      if (attachedFileForTurn && !keepPendingAudio) {
        delete state.sessionPendingAudioFile[requestSessionId];
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
      item.setAttribute("aria-selected", "false");
    });
    btn.classList.add("is-active");
    btn.setAttribute("aria-selected", "true");
    ["info", "tools", "history", "docs"].forEach(function (tab) {
      const el = document.getElementById("tab-" + tab);
      if (el) el.style.display = tab === name ? "block" : "none";
    });
    syncDocumentCenterVisibility(name);
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
    renderDraftWelcome(state.sessionId);
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
        renderDraftWelcome(sid);
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
      // 已載入過的 session 仍可能在背景任務中改變狀態
      // 先重建本地 task UI；若沒有 active task 再直接返回
      restoreSessionTaskUI(sid);
      if (sid === state.sessionId) {
        maybePromptApprovalForCurrentSession(sid);
      }

      const hasActiveTask = listActiveTasksForSession(sid).length > 0;
      if (!hasActiveTask) {
        scrollSessionToBottom(sid);
        return;
      }
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

  // ── Generic file attachment (PDF/DOCX/images/etc.) ──
  // Uses /api/upload/personal which stores into
  // Agent_workspace/line_uploads/{user_id}/ — same pool as LINE Bot uploads.
  window.triggerGenericUpload = function () {
    const fileInput = document.getElementById("genericFileInput");
    if (!fileInput) return;
    fileInput.value = "";
    fileInput.onchange = async function () {
      const file = fileInput.files[0];
      if (!file) return;

      // Size guard: 50 MB cap for web uploads
      const MAX_BYTES = 50 * 1024 * 1024;
      if (file.size > MAX_BYTES) {
        showToast(`檔案過大（${(file.size / 1048576).toFixed(1)} MB），上限 50 MB`, "error");
        return;
      }

      showToast("正在上傳 " + file.name + "...", "info");
      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch("/api/upload/personal", {
          method: "POST",
          credentials: "same-origin",
          body: formData,
        });
        const data = await res.json();
        if (!res.ok || data.status !== "success") {
          throw new Error(data.detail || "Upload failed");
        }

        // Queue as attachment for the NEXT message send
        state.sessionPendingAttachment = state.sessionPendingAttachment || {};
        state.sessionPendingAttachment[state.sessionId] = {
          path: String(data.filepath || ""),
          name: file.name || String(data.filename || ""),
          size: data.size || file.size,
        };
        _renderAttachChip();
        showToast(`已附加「${file.name}」，輸入訊息後送出`, "success");
      } catch (err) {
        showToast("檔案上傳失敗：" + (err.message || "未知錯誤"), "error");
      }
    };
    fileInput.click();
  };

  function _renderAttachChip() {
    const row = document.getElementById("attachChipRow");
    if (!row) return;
    const att = (state.sessionPendingAttachment || {})[state.sessionId];
    if (!att) {
      row.style.display = "none";
      row.innerHTML = "";
      return;
    }
    const sizeKB = att.size ? Math.round(att.size / 1024) : 0;
    const safeName = escapeHtml(att.name || "file");
    row.style.display = "flex";
    row.innerHTML =
      '<div class="page-chat-attach-chip" role="button" tabindex="0">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
      '<polyline points="14 2 14 8 20 8"/></svg>' +
      '<span class="page-chat-attach-chip-name">' + safeName + '</span>' +
      (sizeKB ? '<span class="page-chat-attach-chip-size">' + sizeKB + ' KB</span>' : '') +
      '<button class="page-chat-attach-chip-remove" onclick="window._clearAttachChip()" aria-label="移除附件" title="移除">✕</button>' +
      '</div>';
  }

  window._clearAttachChip = function () {
    if (state.sessionPendingAttachment) {
      delete state.sessionPendingAttachment[state.sessionId];
    }
    _renderAttachChip();
  };

  function _takeSessionAttachment(sessionId) {
    // Pop the attachment (single-use: after send, it's gone)
    if (!state.sessionPendingAttachment) return null;
    const att = state.sessionPendingAttachment[sessionId];
    if (att) {
      delete state.sessionPendingAttachment[sessionId];
      _renderAttachChip();
    }
    return att;
  }

  async function uploadRecordedAudioToDocumentCenter(file) {
    if (!file) return false;
    const MAX_BYTES = 25 * 1024 * 1024;
    if (file.size > MAX_BYTES) {
      showToast("錄音檔過大，請控制在 25 MB 內", "error");
      return false;
    }

    const filename = String(file.name || buildRecordedAudioFilename("webm"));
    showToast("正在儲存錄音到文件中心...", "info");
    const formData = new FormData();
    formData.append("file", file, filename);

    try {
      const res = await fetch("/api/user-documents/upload", {
        method: "POST",
        credentials: "same-origin",
        body: formData,
      });
      const rawText = await res.text();
      let data = null;
      try {
        data = rawText ? JSON.parse(rawText) : null;
      } catch (_err) {
        data = null;
      }
      if (!res.ok || !data || data.status !== "success") {
        throw new Error((data && (data.detail || data.message)) || rawText || ("HTTP " + res.status));
      }
      showToast("錄音已儲存到文件中心", "success");
      loadUserDocuments({ silent: true });
      return true;
    } catch (err) {
      showToast("錄音儲存失敗：" + (err.message || "未知錯誤"), "error");
      return false;
    }
  }

  async function handleRecordedAudioStopped() {
    stopAudioSignalMonitor();
    releaseAudioRecorderStream();
    const chunks = audioRecorderState.chunks.slice();
    const mimeType = audioRecorderState.mimeType || "audio/webm";
    const fileExtension = audioRecorderState.fileExtension || inferAudioExtensionFromMimeType(mimeType);
    const signalPeak = Number(audioRecorderState.signalPeak || 0);
    const inputLabel = audioRecorderState.inputLabel || "";

    audioRecorderState.mediaRecorder = null;
    audioRecorderState.chunks = [];
    audioRecorderState.startedAt = 0;

    if (!chunks.length) {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      showToast("沒有錄到音訊內容，請再試一次。", "error");
      return;
    }

    const blob = new Blob(chunks, { type: mimeType });
    if (!blob.size) {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      showToast("錄音內容為空白，請確認麥克風正常後再試一次。", "error");
      return;
    }

    if (signalPeak < 0.0035) {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      showToast(
        "這段錄音沒有收到有效聲音，可能選到靜音或錯誤的麥克風"
          + (inputLabel ? "（目前裝置：" + inputLabel + "）" : "")
          + "，所以沒有儲存。",
        "error"
      );
      return;
    }

    const filename = buildRecordedAudioFilename(fileExtension);
    const shouldSave = window.confirm("要將剛才的錄音儲存到文件中心嗎？");
    if (!shouldSave) {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      showToast("已取消儲存錄音", "info");
      return;
    }

    const recordedFile = createRecordedAudioFile(blob, filename);
    await uploadRecordedAudioToDocumentCenter(recordedFile);
    resetAudioRecorderState();
    syncAudioRecorderButton();
  }

  async function startAudioRecording() {
    if (!audioRecorderState.supported) {
      showToast("目前瀏覽器不支援直接錄音，請改用音檔上傳。", "error");
      return;
    }
    if (audioRecorderState.isRecording || audioRecorderState.isProcessing) {
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      const audioTrack = stream.getAudioTracks && stream.getAudioTracks()[0];
      if (audioTrack) {
        audioTrack.enabled = true;
        audioRecorderState.inputLabel = audioTrack.label || "";
        await waitForAudioTrackReady(audioTrack, 1200);
      }
      const preferredMimeType = resolveRecordedAudioMimeType();
      const recorderOptions = preferredMimeType ? { mimeType: preferredMimeType } : undefined;
      const mediaRecorder = recorderOptions ? new MediaRecorder(stream, recorderOptions) : new MediaRecorder(stream);

      audioRecorderState.stream = stream;
      audioRecorderState.mediaRecorder = mediaRecorder;
      audioRecorderState.startedAt = Date.now();
      audioRecorderState.mimeType = mediaRecorder.mimeType || preferredMimeType || "";
      audioRecorderState.fileExtension = inferAudioExtensionFromMimeType(audioRecorderState.mimeType);
      audioRecorderState.chunks = [];
      audioRecorderState.isRecording = true;
      audioRecorderState.isProcessing = false;
      audioRecorderState.signalPeak = 0;

      mediaRecorder.ondataavailable = function (event) {
        if (event.data && event.data.size > 0) {
          audioRecorderState.chunks.push(event.data);
        }
      };
      mediaRecorder.onerror = function () {
        resetAudioRecorderState();
        syncAudioRecorderButton();
        showToast("錄音過程發生錯誤，請再試一次。", "error");
      };
      mediaRecorder.onstop = function () {
        handleRecordedAudioStopped().catch(function (err) {
          resetAudioRecorderState();
          syncAudioRecorderButton();
          showToast("錄音處理失敗：" + ((err && err.message) || "未知錯誤"), "error");
        });
      };

      startAudioSignalMonitor(stream);
      mediaRecorder.start(250);
      syncAudioRecorderButton();
      showToast(
        "已開始錄音，再按一次紅色按鈕即可停止。"
          + (audioRecorderState.inputLabel ? " 目前麥克風：" + audioRecorderState.inputLabel : ""),
        "info"
      );
    } catch (err) {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      showToast(explainRecorderError(err), "error");
    }
  }

  function stopAudioRecording() {
    if (!audioRecorderState.mediaRecorder || audioRecorderState.mediaRecorder.state === "inactive") {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      return;
    }

    audioRecorderState.isRecording = false;
    audioRecorderState.isProcessing = true;
    syncAudioRecorderButton();

    try {
      audioRecorderState.mediaRecorder.stop();
    } catch (_err) {
      resetAudioRecorderState();
      syncAudioRecorderButton();
      showToast("停止錄音失敗，請再試一次。", "error");
    }
  }

  window.triggerAudioUpload = function () {
    if (!audioFileInput) return;
    audioFileInput.value = "";
    audioFileInput.onchange = async function () {
      const file = audioFileInput.files[0];
      if (!file) return;

      showToast("正在上傳音檔...", "info");
      const formData = new FormData();
      formData.append("file", file);

      try {
        if (audioUploadBtn) {
          audioUploadBtn.classList.add("is-transcribing");
        }

        const res = await fetch("/workspace/upload", { method: "POST", body: formData });
        const data = await res.json();
        if (data.status !== "success") throw new Error(data.detail || "Upload failed");

        state.sessionPendingAudioFile[state.sessionId] = {
          path: String(data.filepath || ""),
          name: file.name || String(data.filename || ""),
        };
        pruneLocalAudioHandoffPromptFromCache(state.sessionId);
        const safeName = file.name || String(data.filename || "音檔");
        const uploadMessage = "已上傳「" + safeName + "」檔案";
        if (chatInput) {
          chatInput.value = "";
          autoResize(chatInput);
        }
        showToast("已上傳 " + file.name, "success");
        syncComposerState();
        sendMessage(uploadMessage, {
          attachedFile: String(data.filepath || ""),
          uploadHandoff: true,
        });
      } catch (err) {
        showToast("音檔上傳失敗：" + err.message, "error");
      } finally {
        if (audioUploadBtn) {
          audioUploadBtn.classList.remove("is-transcribing");
        }
      }
    };
    audioFileInput.click();
  };

  if (audioRecorderBtn) {
    syncAudioRecorderButton();
    audioRecorderBtn.addEventListener("click", function () {
      if (audioRecorderState.isRecording) {
        stopAudioRecording();
      } else {
        startAudioRecording();
      }
    });
  }

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
        // Enter in stop mode = stop, not send
        if (sendBtn.dataset.mode === "stop") {
          stopAllActiveTasks();
        } else {
          sendMessage(chatInput.value);
        }
      }
    });
    sendBtn.addEventListener("click", function () {
      if (sendBtn.dataset.mode === "stop") {
        stopAllActiveTasks();
      } else {
        sendMessage(chatInput.value);
      }
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

  const userDocModal = document.getElementById("userDocModal");
  if (userDocModal) {
    userDocModal.addEventListener("click", function (event) {
      if (event.target === userDocModal) {
        closeUserDocumentModal();
      }
    });
  }

  const userDocModalFullscreenBtn = document.getElementById("userDocModalFullscreenBtn");
  if (userDocModalFullscreenBtn) {
    userDocModalFullscreenBtn.addEventListener("click", function () {
      toggleUserDocumentModalFullscreen();
    });
  }

  document.addEventListener("fullscreenchange", syncUserDocumentModalFullscreenUi);
  document.addEventListener("webkitfullscreenchange", syncUserDocumentModalFullscreenUi);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && userDocModalPseudoFullscreen) {
      exitUserDocumentModalFullscreen();
    }
  });
  window.addEventListener("pagehide", function () {
    resetAudioRecorderState();
    syncAudioRecorderButton();
  });

  syncDocumentCenterVisibility("info");

  setInterval(updateSessionDuration, 1000);
  updateSessionDuration();
  updateStats();
  loadModels();

  hydrateAuthFromServer().finally(function () {
    loadSideInfo();
    loadUserDocuments({ silent: true });
    renderConversationList();
    window.loadConversationById(state.sessionId, true);
    syncComposerState();
  });
})();
