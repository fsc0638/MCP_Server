(function () {
  "use strict";

  const form = document.getElementById("loginForm");
  const emailEl = document.getElementById("email");
  const pwdEl = document.getElementById("password");
  const loginBtn = document.getElementById("loginBtn");
  const togglePwd = document.getElementById("togglePwd");
  const cfg = window.KWAY_CONFIG || {};

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
    }, 2800);
  }

  function isValidEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  if (togglePwd && pwdEl) {
    togglePwd.addEventListener("click", function () {
      pwdEl.type = pwdEl.type === "password" ? "text" : "password";
    });
  }

  if (!form) return;

  if (emailEl && typeof cfg.DEMO_EMAIL === "string" && cfg.DEMO_EMAIL.trim()) {
    emailEl.value = cfg.DEMO_EMAIL.trim();
  }
  if (pwdEl && typeof cfg.DEMO_PASSWORD === "string" && cfg.DEMO_PASSWORD.trim()) {
    pwdEl.value = cfg.DEMO_PASSWORD;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const email = (emailEl?.value || "").trim();
    const password = pwdEl?.value || "";

    if (!isValidEmail(email)) {
      showToast("Please enter a valid email", "error");
      return;
    }
    if (password.length < 4) {
      showToast("Password must be at least 4 characters", "error");
      return;
    }

    if (loginBtn) {
      loginBtn.classList.add("loading");
      loginBtn.disabled = true;
    }

    const name = email.split("@")[0] || "User";
    const initials = name.slice(0, 2).toUpperCase();
    sessionStorage.setItem(
      "kway_user",
      JSON.stringify({
        name: name,
        initials: initials,
        email: email,
        dept: "MCP Workspace",
        provider: "password",
      })
    );

    showToast("Login successful", "success");
    setTimeout(function () {
      window.location.href = "chat.html";
    }, 700);
  });

  window.onload = function () {
    if (cfg.GOOGLE_CLIENT_ID) {
      console.log("[GoogleLogin] Initializing GSI with Client ID:", cfg.GOOGLE_CLIENT_ID);
      
      // Proactive check: Google OAuth DOES NOT support IP addresses (127.0.0.1)
      if (location.hostname === "127.0.0.1") {
        console.error("[GoogleLogin] Error: Google OAuth requires 'localhost' instead of '127.0.0.1'.");
        showToast("錯誤：Google 登入不支援 IP 位址，請改用 localhost", "error");
        
        // Add a visible warning for the user
        const hint = document.querySelector(".page-login-subtitle");
        if (hint) {
          hint.innerHTML = '<span style="color:#ef4444;font-weight:700;">⚠ 請改用 <a href="http://localhost:8500/pages/login.html">localhost</a> 網址以啟用 Google 登入</span>';
        }
      }

      google.accounts.id.initialize({
        client_id: cfg.GOOGLE_CLIENT_ID,
        callback: handleCredentialResponse,
        use_fedcm_for_prompt: false, 
      });
    }
  };

  async function handleCredentialResponse(response) {
    console.log("[GoogleLogin] Callback received. Credential present:", !!response.credential);
    if (!response.credential) {
      showToast("未取得驗證憑證", "error");
      return;
    }
    if (loginBtn) {
      loginBtn.classList.add("loading");
      loginBtn.disabled = true;
    }
    try {
      const res = await fetch("/api/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: response.credential }),
      });
      if (!res.ok) throw new Error("Auth verify failed");
      const data = await res.json();
      if (data.status === "success") {
        sessionStorage.setItem("kway_user", JSON.stringify(data.user));
        showToast("Google 登入成功", "success");
        setTimeout(() => (window.location.href = "chat.html"), 800);
      } else {
        throw new Error(data.message || "Auth failed");
      }
    } catch (err) {
      console.error("[GoogleLogin] Verification error:", err);
      showToast("Google Login failed: " + err.message, "error");
      if (loginBtn) {
        loginBtn.classList.remove("loading");
        loginBtn.disabled = false;
      }
    }
  }

  window.socialLogin = function (provider) {
    if (provider === "Google") {
      if (!cfg.GOOGLE_CLIENT_ID) {
        showToast("Google Client ID 未設定", "error");
        return;
      }
      
      // Bypassing Google's "Exponential Backoff" (the reason for "skipped: unknown_reason")
      // This allows the prompt to show up even if the user dismissed it earlier.
      document.cookie = "g_state=; expires=Thu, 01 Jan 1970 00:00:01 GMT; path=/";

      if (location.hostname === "127.0.0.1") {
        alert("Google 登入不支援 127.0.0.1，請將網址改為 localhost:8500");
        location.href = location.href.replace("127.0.0.1", "localhost");
        return;
      }

      console.log("[GoogleLogin] Prompting account selection with cooldown bypass...");
      google.accounts.id.prompt((notification) => {
        const reason = notification.getNotDisplayedReason();
        const skipped = notification.getSkippedReason();
        console.log("[GoogleLogin] Prompt Notification:", {
          moment: notification.getMomentType(),
          notDisplayed: notification.isNotDisplayed(),
          reason: reason,
          skipped: skipped
        });

        if (notification.isNotDisplayed()) {
          let errorMsg = "無法開啟 Google 視窗：" + (reason || "未知原因");
          
          if (reason === "browser_denied" || reason === "suppressed_by_user") {
            errorMsg = "⚠ 您的瀏覽器封鎖了第三方登入。請點擊網址列左側圖示，並開啟「第三方登入 / 帳號與密碼」權限。";
          } else if (reason === "opt_out_or_no_session") {
            errorMsg = "請先登入您的 Google 帳戶後再試。";
          }
          
          showToast(errorMsg, "error");
          console.error("[GoogleLogin] Prompt not displayed. Detailed reason:", reason, "| Skipped reason:", skipped);
          console.error("[GoogleLogin] 疑難排解：請檢查 Chrome 網址列左方圖示 -> 網站設定 -> 允許「第三方登入行為」。");
        }
      });
    } else {
      showToast(provider + " sign-in is not enabled in MCP mode", "info");
    }
  };

  const params = new URLSearchParams(location.search);
  if (params.get("mode") === "sso") {
    const titleEl = document.querySelector(".page-login-title");
    const subtitleEl = document.querySelector(".page-login-subtitle");
    if (titleEl) titleEl.textContent = "Enterprise SSO Sign-in";
    if (subtitleEl) subtitleEl.textContent = "Use your organization identity to continue.";
  }
})();
