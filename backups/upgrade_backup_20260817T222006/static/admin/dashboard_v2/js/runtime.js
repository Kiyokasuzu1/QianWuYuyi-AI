/* =============================================================
 * runtime.js —— Runtime 页面渲染
 *
 * 数据源:
 *  - /api/dashboard/v2/runtime/status
 *  - /api/dashboard/v2/runtime/tasks
 *  - /api/dashboard/v2/runtime/events
 *  - /api/dashboard/v2/runtime/live2d  (Step 8.4.6: 只读联动 Runtime Snapshot)
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;
  const TracePanel = window.YuyiTracePanel;

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
  function formatUptime(s) {
    if (s === null || s === undefined) return "--";
    s = Number(s);
    if (!isFinite(s) || s < 0) return "--";
    if (s < 60) return s.toFixed(0) + "s";
    if (s < 3600) return Math.floor(s / 60) + "m " + Math.floor(s % 60) + "s";
    return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
  }
  function statusClass(state) {
    if (!state) return "is-offline";
    if (state === "running" || state === "started") return "is-online";
    if (state === "created" || state === "stopped") return "is-degraded";
    return "is-offline";
  }
  function toast(msg) {
    const el = $("yuyiToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    setTimeout(() => el.classList.remove("is-visible"), 2400);
  }

  // ---------------------------------------------------------
  // Live2D TracePanel 实例(单例,Step 8.4.6)
  // ---------------------------------------------------------
  let _live2dTracePanel = null;
  function _getLive2dTracePanel() {
    if (_live2dTracePanel) return _live2dTracePanel;
    const root = $("yrt-live2d-trace");
    if (!root || !TracePanel) return null;
    _live2dTracePanel = new TracePanel(root, {
      title: "🪶 Live2D 数据来源",
      defaultExpanded: false,
    });
    return _live2dTracePanel;
  }

  async function loadStatus() {
    if (!API || !API.runtimeStatus) {
      toast("API 客户端缺少 runtimeStatus");
      return;
    }
    try {
      const r = await API.runtimeStatus();
      const d = r.data || {};
      setText("yrt-initialized", d.initialized ? "✓" : "✗");
      setText("yrt-running", d.running ? "✓" : "✗");
      setText("yrt-uptime", formatUptime(d.uptime));
      setText("yrt-current-state", d.current_state || "--");
      setText("yrt-tick-count", d.tick_count || 0);
      setText("yrt-bridge-error", d.bridge_error || "无");
      const tag = $("yrt-state-tag");
      if (tag) {
        tag.textContent = d.current_state || "unknown";
        tag.className = "yuyi-card__tag " + statusClass(d.current_state);
      }
      if (r.fallback) toast("Runtime 数据不可用,展示 fallback");
    } catch (e) {
      toast("加载 runtime status 失败: " + (e && e.message ? e.message : e));
    }
  }

  async function loadTasks() {
    if (!API || !API.runtimeTasks) return;
    try {
      const r = await API.runtimeTasks();
      const list = $("yrt-tasks-list");
      if (!list) return;
      const tasks = (r.data && r.data.tasks) || [];
      if (!tasks.length) {
        list.innerHTML = '<li class="yuyi-list__empty">暂无 Lifecycle Task</li>';
        return;
      }
      list.innerHTML = "";
      tasks.forEach((t) => {
        const li = document.createElement("li");
        const dot = t.status === "running" ? "is-online" : "is-offline";
        li.innerHTML = `<span>${t.task_name}</span><b class="${dot}">${t.status} · err=${t.error_count}</b>`;
        list.appendChild(li);
      });
    } catch (e) {
      // 静默
    }
  }

  async function loadEvents() {
    if (!API || !API.runtimeEvents) return;
    try {
      const r = await API.runtimeEvents(20);
      const list = $("yrt-events-list");
      if (!list) return;
      const ticks = (r.data && r.data.ticks) || [];
      if (!ticks.length) {
        list.innerHTML = '<li class="yuyi-events__item yuyi-events__item--empty">暂无事件</li>';
        return;
      }
      list.innerHTML = "";
      ticks.forEach((ev) => {
        const li = document.createElement("li");
        li.className = "yuyi-events__item";
        li.innerHTML = `
          <b>${ev.event_type || "unknown"}</b>
          <span>${ev.source || ""}</span>
          <small>${ev.received_at || ev.timestamp || ""}</small>
        `;
        list.appendChild(li);
      });
    } catch (e) {
      // 静默
    }
  }

  // ---------------------------------------------------------
  // Live2D Status Card(Step 8.4.6)—— 只读联动 Runtime Snapshot
  // ---------------------------------------------------------
  async function loadLive2d() {
    if (!API || !API.runtimeLive2d) return;
    const card = document.querySelector('[data-block="runtime-live2d"]');
    if (!card) return;
    const tag = $("yrt-live2d-tag");
    const status = $("yrt-live2d-status");
    try {
      // 接口直接返回 envelope(不是 unwrap)
      const env = await API.runtimeLive2d();
      const d = (env && env.data) || {};
      const isFallback = env && env.fallback === true;
      const conf = typeof env.confidence === "number" ? env.confidence : 0;

      // tag 状态
      if (tag) {
        if (isFallback) {
          tag.textContent = "fallback";
          tag.className = "yuyi-card__tag is-offline";
        } else if (d.available) {
          tag.textContent = "🟢 connected";
          tag.className = "yuyi-card__tag is-online";
        } else {
          tag.textContent = "✕ no-signal";
          tag.className = "yuyi-card__tag is-offline";
        }
      }

      // 状态描述
      if (status) {
        if (isFallback) {
          // 严格不显示假状态
          status.textContent = "Runtime snapshot unavailable";
          status.classList.add("yuyi-empty");
        } else if (!d.available) {
          status.textContent = "Runtime snapshot unavailable";
          status.classList.add("yuyi-empty");
        } else {
          status.textContent = "Live2D 状态来自 Runtime Snapshot(只读)";
          status.classList.remove("yuyi-empty");
        }
      }

      // 字段
      setText("yrt-live2d-expression", d.expression || "--");
      setText("yrt-live2d-motion", d.motion || "--");
      setText("yrt-live2d-reason", d.reason || "runtime_snapshot");
      setText("yrt-live2d-source", "Runtime Snapshot");
      setText("yrt-live2d-confidence", API.formatConfidence ? API.formatConfidence(conf) : (conf ? Math.round(conf * 100) + "%" : "--"));
      setText("yrt-live2d-timestamp", d.timestamp || "--");

      // TracePanel 更新(从 envelope 提取)
      const panel = _getLive2dTracePanel();
      if (panel) {
        // 检查是否在 confidence toggle 开启状态
        const enabled = API.getConfidenceToggle ? API.getConfidenceToggle() : false;
        const wrap = $("yrt-live2d-trace");
        if (wrap) {
          wrap.style.display = enabled ? "" : "none";
        }
        panel.set(env);
      }
    } catch (e) {
      if (tag) {
        tag.textContent = "error";
        tag.className = "yuyi-card__tag is-offline";
      }
      if (status) {
        status.textContent = "Runtime snapshot unavailable";
        status.classList.add("yuyi-empty");
      }
      setText("yrt-live2d-expression", "--");
      setText("yrt-live2d-motion", "--");
      setText("yrt-live2d-reason", "--");
      setText("yrt-live2d-confidence", "--");
      setText("yrt-live2d-timestamp", "--");
    }
  }

  async function refresh() {
    await Promise.all([loadStatus(), loadTasks(), loadEvents(), loadLive2d()]);
  }

  // ---------------------------------------------------------
  // 监听全局 confidence toggle 事件
  // ---------------------------------------------------------
  function bindConfidenceToggle() {
    window.addEventListener("yuyi:confidence-toggle", (e) => {
      const enabled = e && e.detail && e.detail.enabled;
      const wrap = $("yrt-live2d-trace");
      if (wrap) wrap.style.display = enabled ? "" : "none";
    });
  }

  function init() {
    const root = document.querySelector('[data-page-id="runtime"]');
    if (!root) return; // 当前路由不在 runtime
    bindConfidenceToggle();
    refresh();
    const btn = document.getElementById("yrt-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
