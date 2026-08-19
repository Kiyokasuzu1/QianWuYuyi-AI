/* =============================================================
 * ws.js —— Dashboard V2 WebSocket 客户端（占位）
 *
 * 职责:
 *  - 维护 WebSocket 连接（Phase 5.0-E 接入）
 *  - 暴露 subscribe / unsubscribe / connect / close
 *  - 当前阶段连接失败时回退到 polling（通过 api.js）
 * ============================================================= */
(function (global) {
  "use strict";

  const WS_PATH = "/api/dashboard/v2/ws";

  class DashboardWS {
    constructor() {
      this._ws = null;
      this._subs = new Set();
      this._pollTimer = null;
      this._fallbackPoll = false;
    }

    /**
     * 启动连接。失败时自动 fallback 到 polling。
     */
    connect() {
      try {
        const proto = (location.protocol === "https:") ? "wss" : "ws";
        const url = proto + "://" + location.host + WS_PATH;
        const ws = new WebSocket(url);
        this._ws = ws;
        ws.addEventListener("open", () => {
          this._fallbackPoll = false;
          this._stopPolling();
        });
        ws.addEventListener("message", (ev) => {
          let payload = null;
          try { payload = JSON.parse(ev.data); } catch (e) { return; }
          this._dispatch(payload);
        });
        ws.addEventListener("close", () => {
          this._ws = null;
          this._startPolling();
        });
        ws.addEventListener("error", () => {
          // 浏览器无法建立 ws 升级,关闭后由 close 事件启动 polling
        });
      } catch (e) {
        // 浏览器不支持 ws 时直接 polling
        this._startPolling();
      }
    }

    close() {
      this._stopPolling();
      if (this._ws) {
        try { this._ws.close(); } catch (e) {}
        this._ws = null;
      }
    }

    subscribe(fn) {
      if (typeof fn === "function") {
        this._subs.add(fn);
      }
    }

    unsubscribe(fn) {
      this._subs.delete(fn);
    }

    _dispatch(payload) {
      this._subs.forEach((fn) => {
        try { fn(payload); } catch (e) { /* 静默 */ }
      });
    }

    _startPolling() {
      if (this._pollTimer || this._fallbackPoll) return;
      this._fallbackPoll = true;
      const poll = async () => {
        if (!this._fallbackPoll) return;
        try {
          if (global.YuyiDashboardAPI && global.YuyiDashboardAPI.events) {
            const r = await global.YuyiDashboardAPI.events(20);
            if (r && r.data && Array.isArray(r.data.events)) {
              this._dispatch({ type: "events", events: r.data.events, fallback: !!r.fallback });
            }
          }
        } catch (e) { /* 静默 */ }
      };
      this._pollTimer = setInterval(poll, 5000);
      poll();
    }

    _stopPolling() {
      if (this._pollTimer) {
        clearInterval(this._pollTimer);
        this._pollTimer = null;
      }
    }
  }

  global.YuyiDashboardWS = new DashboardWS();
})(window);
