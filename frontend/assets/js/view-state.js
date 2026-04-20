/* ============================================================
   View State Memory — 通用頁面狀態記憶
   ------------------------------------------------------------
   每個頁面/分區綁定一個 key，把狀態序列化存進 sessionStorage。
   頁面 render 時 restore() 取回之前的狀態並套用。

   Usage:
     const vs = viewState("wf_landing");
     // Save any shape
     vs.save({ tab: "department", search: "" });
     vs.update({ scrollTop: 120 });      // merge-update one field
     // Restore
     const s = vs.restore();              // {} if never saved
     const tab = vs.restore("tab", "all"); // key + default shortcut

   Why sessionStorage:
     - Lifetime: until tab close (long enough for in-session navigation
       like 回選單 / open-drawer-then-close / tab-switch)
     - Per-tab isolation (two browser tabs don't overwrite each other)
     - No cross-origin concerns
   ============================================================ */

(function () {
  "use strict";
  if (window.viewState) return;  // guard against double-load

  const KEY_PREFIX = "kway_view_";

  function _read(key) {
    try {
      return JSON.parse(sessionStorage.getItem(KEY_PREFIX + key) || "{}") || {};
    } catch (_) { return {}; }
  }

  function _write(key, obj) {
    try {
      sessionStorage.setItem(KEY_PREFIX + key, JSON.stringify(obj || {}));
    } catch (_) {}
  }

  window.viewState = function (key) {
    if (!key) throw new Error("viewState: key required");
    return {
      /** Replace the entire stored state for this key */
      save(obj) { _write(key, obj); },
      /** Shallow-merge an update into the stored state */
      update(partial) {
        const cur = _read(key);
        _write(key, Object.assign({}, cur, partial || {}));
      },
      /**
       * restore() / restore("field") / restore("field", defaultValue)
       */
      restore(field, defaultValue) {
        const obj = _read(key);
        if (field == null) return obj;
        return (field in obj) ? obj[field] : defaultValue;
      },
      /** Delete all stored state for this key */
      clear() {
        try { sessionStorage.removeItem(KEY_PREFIX + key); } catch (_) {}
      },
    };
  };

  /**
   * Clear ALL stored view states (called on logout to prevent leaking
   * one user's navigation state into another user's session).
   */
  window.clearAllViewStates = function () {
    try {
      Object.keys(sessionStorage)
        .filter(k => k.startsWith(KEY_PREFIX))
        .forEach(k => sessionStorage.removeItem(k));
    } catch (_) {}
  };
})();
