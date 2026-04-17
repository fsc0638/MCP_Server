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

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const email = (emailEl?.value || "").trim().toLowerCase();
    const password = pwdEl?.value || "";
    const remember = document.getElementById("remember")?.checked !== false;

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

    try {
      // Call server-side login: validates password AND sets mcp_session cookie
      // so that downstream features (identity verify, logout, memory, workflows)
      // all work identically to LINE login.
      const res = await fetch("/api/auth/password-login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: email, password: password, remember: remember }),
      });

      if (!res.ok) {
        const err = await res.json().catch(function () { return {}; });
        if (res.status === 401) {
          showToast("帳號或密碼錯誤", "error");
        } else if (res.status === 400) {
          showToast("輸入格式錯誤：" + (err.detail || ""), "error");
        } else {
          showToast("登入失敗（" + res.status + "）", "error");
        }
        if (loginBtn) { loginBtn.classList.remove("loading"); loginBtn.disabled = false; }
        return;
      }

      const data = await res.json();
      if (data.status === "success" && data.user) {
        // Mirror user info to sessionStorage for fast paint on next page
        sessionStorage.setItem("kway_user", JSON.stringify(data.user));
        showToast("登入成功", "success");
        setTimeout(function () { window.location.href = "chat.html"; }, 600);
      } else {
        showToast("登入失敗", "error");
        if (loginBtn) { loginBtn.classList.remove("loading"); loginBtn.disabled = false; }
      }
    } catch (err) {
      console.error("[Login] Error:", err);
      showToast("網路錯誤：" + err.message, "error");
      if (loginBtn) { loginBtn.classList.remove("loading"); loginBtn.disabled = false; }
    }
  });

  // LINE-only social login (Google removed per product decision)
  window.socialLogin = function (provider) {
    if (provider === "LINE") {
      // Start LINE Login (web) — server will handle redirect + callback.
      window.location.href = "/api/auth/line/login";
    } else {
      showToast(provider + " 登入已停用，請改用表單或 LINE 登入", "info");
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
