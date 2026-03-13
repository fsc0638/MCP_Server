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

  window.socialLogin = function (provider) {
    showToast(provider + " sign-in is not enabled in MCP mode", "info");
  };

  const params = new URLSearchParams(location.search);
  if (params.get("mode") === "sso") {
    const titleEl = document.querySelector(".page-login-title");
    const subtitleEl = document.querySelector(".page-login-subtitle");
    if (titleEl) titleEl.textContent = "Enterprise SSO Sign-in";
    if (subtitleEl) subtitleEl.textContent = "Use your organization identity to continue.";
  }
})();
