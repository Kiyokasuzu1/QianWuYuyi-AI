/* =============================================================
 * runtime_overview.js —— Phase 7.0 运行中心(Runtime Center)页面
 *
 * 数据源:
 *  - /api/dashboard/v2/runtime/services   (4 服务状态总览)
 *  - /api/dashboard/v2/runtime/lifecycle  (Runtime 生命周期)
 *  - /api/dashboard/v2/runtime/traces     (请求链路追踪)
 *
 * 设计:
 *  - 与 runtime.js 完全独立,不影响原有 Runtime 页面
 *  - 支持手动刷新 + 可选自动刷新(5s)
 *  - 所有异常吞掉,展示 fallback 状态
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;

  function $(id) { return document.getElementById(id); }
  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = (value === undefined || value === null) ? "--" : String(value);
  }
  function setClass(id, cls) {
    const el = $(id);
    if (!el) return;
    el.classList.remove("is-online", "is-degraded", "is-critical", "is-offline", "is-healthy");
    if (cls) el.classList.add(cls);
  }
  function toast(msg) {
    const el = $("yuyiToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    setTimeout(() => el.classList.remove("is-visible"), 2400);
  }
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function formatUptime(s) {
    if (s === null || s === undefined) return "--";
    s = Number(s);
    if (!isFinite(s) || s < 0) return "--";
    if (s < 60) return s.toFixed(0) + "s";
    if (s < 3600) return Math.floor(s / 60) + "m " + Math.floor(s % 60) + "s";
    return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
  }

  function formatAge(seconds) {
    if (seconds === null || seconds === undefined) return "--";
    const s = Number(seconds);
    if (!isFinite(s) || s < 0) return "--";
    if (s < 60) return s.toFixed(0) + " 秒前";
    if (s < 3600) return Math.floor(s / 60) + " 分钟前";
    return Math.floor(s / 3600) + " 小时前";
  }

  function statusClass(state) {
    if (!state) return "is-offline";
    const s = String(state).toLowerCase();
    if (s === "running" || s === "connected" || s === "started" || s === "online") return "is-online";
    if (s === "idle" || s === "created" || s === "stopped" || s === "stale") return "is-degraded";
    if (s === "error" || s === "failed" || s === "critical") return "is-critical";
    return "is-offline";
  }

  function statusDot(state) {
    const cls = statusClass(state);
    const labels = {
      "is-online": "🟢",
      "is-degraded": "🟡",
      "is-critical": "🔴",
      "is-offline": "⚫",
    };
    return labels[cls] || "⚫";
  }

  // ---------------------------------------------------------
  // 1. 服务状态
  // ---------------------------------------------------------
  async function loadServices() {
    if (!API || !API.runtimeServices) return;
    const grid = $("yroc-services-grid");
    const tag = $("yroc-services-tag");
    try {
      const r = await API.runtimeServices();
      const d = r.data || {};
      const services = [
        { key: "api_server", label: "API Server", data: d.api_server },
        { key: "runtime", label: "Runtime Core", data: d.runtime },
        { key: "initiative_sender", label: "Initiative Sender", data: d.initiative_sender },
        { key: "agent_server", label: "Agent Server", data: d.agent_server },
      ];

      if (grid) {
        grid.innerHTML = services.map((svc) => {
          const sd = svc.data || {};
          const status = sd.status || "unknown";
          const dot = statusDot(status);
          const cls = statusClass(status);
          const fb = sd.fallback === true;
          const fbNote = fb ? '<small style="color: var(--yuyi-text-mute);">(fallback)</small>' : "";

          // Agent Server 特殊字段
          let extra = "";
          if (svc.key === "agent_server") {
            const host = sd.host || "--";
            const port = sd.port || "--";
            const agents = sd.total_agents !== undefined ? sd.total_agents : "--";
            const auth = sd.authenticated_agents !== undefined ? sd.authenticated_agents : "--";
            const stale = sd.stale === true;
            extra = `
              <div class="yroc-svc__detail">
                <span>${esc(host)}:${esc(port)}</span>
                <span>agents: ${esc(agents)} (auth: ${esc(auth)})</span>
                ${stale ? '<span style="color: var(--yuyi-degraded);">stale</span>' : ""}
              </div>`;
          } else if (svc.key === "api_server") {
            const task = sd.current_task || "--";
            const uptime = formatUptime(sd.uptime_seconds);
            extra = `
              <div class="yroc-svc__detail">
                <span>task: ${esc(task)}</span>
                <span>uptime: ${esc(uptime)}</span>
              </div>`;
          } else if (svc.key === "runtime") {
            const init = sd.initialized ? "✓" : "✗";
            const running = sd.running ? "✓" : "✗";
            extra = `
              <div class="yroc-svc__detail">
                <span>initialized: ${init}</span>
                <span>running: ${running}</span>
              </div>`;
          } else if (svc.key === "initiative_sender") {
            const alive = sd.initiative_sender_alive !== undefined
              ? (sd.initiative_sender_alive ? "alive" : "stale")
              : "--";
            extra = `
              <div class="yroc-svc__detail">
                <span>heartbeat: ${esc(alive)}</span>
              </div>`;
          }

          return `
            <div class="yroc-svc-card ${cls}">
              <div class="yroc-svc__header">
                <span class="yroc-svc__dot">${dot}</span>
                <span class="yroc-svc__name">${esc(svc.label)}</span>
                <span class="yroc-svc__status ${cls}">${esc(status)} ${fbNote}</span>
              </div>
              ${extra}
            </div>`;
        }).join("");
      }

      if (tag) {
        const allRunning = services.every((s) => {
          const st = (s.data && s.data.status) || "unknown";
          return st === "running" || st === "connected";
        });
        tag.textContent = allRunning ? "all running" : "degraded";
        tag.className = "yuyi-card__tag " + (allRunning ? "is-online" : "is-degraded");
      }
    } catch (e) {
      if (grid) grid.innerHTML = '<p class="yuyi-empty">服务状态加载失败: ' + esc(e && e.message ? e.message : e) + '</p>';
      if (tag) { tag.textContent = "error"; tag.className = "yuyi-card__tag is-offline"; }
    }
  }

  // ---------------------------------------------------------
  // 2. 运行生命周期
  // ---------------------------------------------------------
  async function loadLifecycle() {
    if (!API || !API.runtimeLifecycle) return;
    const tag = $("yroc-lifecycle-tag");
    const errBox = $("yroc-lifecycle-error");
    try {
      const r = await API.runtimeLifecycle();
      const d = r.data || {};

      setText("yroc-status", d.status || "unknown");
      setText("yroc-uptime", formatUptime(d.uptime_seconds));
      setText("yroc-current-task", d.current_task || "idle");
      setText("yroc-last-event", d.last_event || "--");
      setText("yroc-last-response", formatAge(d.last_response_age_seconds));
      setText("yroc-last-trace-id", d.last_trace_id || "--");
      setText("yroc-request-count", d.request_count || 0);
      setText("yroc-success-count", d.success_count || 0);
      setText("yroc-failure-count", d.failure_count || 0);

      if (tag) {
        tag.textContent = d.status || "unknown";
        tag.className = "yuyi-card__tag " + statusClass(d.status);
      }
      if (errBox) {
        if (r.fallback) {
          errBox.textContent = "数据不可用: " + (r.fallback_reason || "unknown");
          errBox.style.display = "";
        } else if (d.last_error) {
          errBox.textContent = "最近错误: " + d.last_error;
          errBox.style.display = "";
        } else {
          errBox.style.display = "none";
        }
      }
    } catch (e) {
      if (tag) { tag.textContent = "error"; tag.className = "yuyi-card__tag is-offline"; }
      if (errBox) {
        errBox.textContent = "加载失败: " + (e && e.message ? e.message : e);
        errBox.style.display = "";
      }
    }
  }

  // ---------------------------------------------------------
  // 3. 请求链路追踪
  // ---------------------------------------------------------
  async function loadTraces() {
    if (!API || !API.runtimeTraces) return;
    const list = $("yroc-traces-list");
    const tag = $("yroc-traces-tag");
    try {
      const r = await API.runtimeTraces(20);
      const traces = (r.data && r.data.traces) || [];
      if (!list) return;

      if (!traces.length) {
        list.innerHTML = '<li class="yuyi-list__empty">暂无请求记录</li>';
        if (tag) { tag.textContent = "0 traces"; tag.className = "yuyi-card__tag is-offline"; }
        return;
      }

      list.innerHTML = traces.map((t) => {
        const success = t.success === true;
        const dot = success ? "🟢" : "🔴";
        const cls = success ? "is-online" : "is-critical";
        const duration = t.total_duration_ms !== undefined ? t.total_duration_ms + "ms" : "--";
        const source = t.reply_source || "--";
        const preview = esc(t.user_message_preview || "").slice(0, 60);
        const replyPreview = esc(t.reply_preview || "").slice(0, 80);
        const traceId = esc(t.trace_id || "");
        const startedAt = esc(t.started_at || "");

        // stages timeline
        const stages = Array.isArray(t.stages) ? t.stages : [];
        const stagesHtml = stages.length
          ? '<div class="yroc-trace__stages">' + stages.map((s) => {
              const sCls = s.error ? "is-critical" : "is-online";
              const sDur = s.duration_ms !== undefined && s.duration_ms !== null ? s.duration_ms + "ms" : "";
              return '<span class="yroc-trace__stage ' + sCls + '" title="' + esc(s.error || "") + '">' +
                esc(s.name) + (sDur ? ' <small>' + sDur + '</small>' : '') +
                '</span>';
            }).join('<span class="yroc-trace__arrow">→</span>') + '</div>'
          : "";

        const errorHtml = t.error
          ? '<div class="yroc-trace__error">⚠ ' + esc(t.error) + '</div>'
          : "";

        return (
          '<li class="yroc-trace-item ' + cls + '">' +
            '<div class="yroc-trace__header">' +
              '<span class="yroc-trace__dot">' + dot + '</span>' +
              '<span class="yroc-trace__id" title="' + traceId + '">' + traceId + '</span>' +
              '<span class="yroc-trace__time">' + startedAt + '</span>' +
              '<span class="yroc-trace__duration">' + duration + '</span>' +
              '<span class="yroc-trace__source">[' + esc(source) + ']</span>' +
            '</div>' +
            '<div class="yroc-trace__msg">📩 ' + preview + '</div>' +
            (replyPreview ? '<div class="yroc-trace__reply">💬 ' + replyPreview + '</div>' : '') +
            stagesHtml +
            errorHtml +
          '</li>'
        );
      }).join("");

      if (tag) {
        tag.textContent = traces.length + " traces";
        tag.className = "yuyi-card__tag is-online";
      }
    } catch (e) {
      if (list) list.innerHTML = '<li class="yuyi-list__empty">链路追踪加载失败: ' + esc(e && e.message ? e.message : e) + '</li>';
      if (tag) { tag.textContent = "error"; tag.className = "yuyi-card__tag is-offline"; }
    }
  }

  // ---------------------------------------------------------
  // 刷新 + 自动刷新
  // ---------------------------------------------------------
  let _autoRefreshTimer = null;

  async function refresh() {
    await Promise.all([loadServices(), loadLifecycle(), loadTraces()]);
  }

  function startAutoRefresh() {
    stopAutoRefresh();
    _autoRefreshTimer = setInterval(() => {
      refresh().catch(() => {});
    }, 5000);
  }

  function stopAutoRefresh() {
    if (_autoRefreshTimer) {
      clearInterval(_autoRefreshTimer);
      _autoRefreshTimer = null;
    }
  }

  function init() {
    const root = document.querySelector('[data-page-id="runtime-center"]');
    if (!root) return;

    refresh();

    const btn = $("yroc-refresh");
    if (btn) {
      btn.addEventListener("click", () => {
        refresh();
        toast("已刷新");
      });
    }

    const autoCb = $("yroc-auto-refresh");
    if (autoCb) {
      // 从 localStorage 恢复偏好
      try {
        const saved = localStorage.getItem("yuyi.runtime_center.auto_refresh");
        if (saved === "1") {
          autoCb.checked = true;
          startAutoRefresh();
        }
      } catch (e) { /* 静默 */ }

      autoCb.addEventListener("change", () => {
        if (autoCb.checked) {
          startAutoRefresh();
          toast("自动刷新已开启(5s)");
        } else {
          stopAutoRefresh();
          toast("自动刷新已关闭");
        }
        try {
          localStorage.setItem("yuyi.runtime_center.auto_refresh", autoCb.checked ? "1" : "0");
        } catch (e) { /* 静默 */ }
      });
    }

    // 页面不可见时暂停自动刷新,节省资源
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        stopAutoRefresh();
      } else if (autoCb && autoCb.checked) {
        startAutoRefresh();
        refresh();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
