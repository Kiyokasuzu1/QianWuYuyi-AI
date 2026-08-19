/* =============================================================
 * overview.js —— Overview 页面渲染
 *
 * 职责:
 *  - 调用 api.overview() 拉取数据
 *  - 渲染到 DOM(健康 / 情绪 / Runtime / 目标 / 兴趣 / Live2D 占位)
 *  - 渲染事件流(右侧)
 *  - 启动 WebSocket 订阅(失败回退到 polling)
 *  - 绑定刷新按钮
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;
  const WS = window.YuyiDashboardWS;

  // ----------------------------------------------------------
  // 工具
  // ----------------------------------------------------------
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

  function healthClass(status) {
    if (!status) return "is-offline";
    const s = String(status).toLowerCase();
    if (s === "healthy") return "is-online";
    if (s === "degraded") return "is-degraded";
    if (s === "critical") return "is-critical";
    return "is-offline";
  }

  function toast(msg, duration) {
    const el = $("yuyiToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    setTimeout(() => el.classList.remove("is-visible"), duration || 2400);
  }

  // ----------------------------------------------------------
  // 渲染
  // ----------------------------------------------------------
  function renderOverview(data, fallback) {
    // Online
    setText("yuyiOnlinePill", data.online ? "在线" : "离线");
    $("yuyiOnlinePill").className = "yuyi-sidebar__pill " + (data.online ? "is-online" : "is-offline");

    // Runtime
    const rs = data.runtime_status || {};
    const tag = rs.initialized && rs.is_running ? "running" : (rs.initialized ? "idle" : "init");
    setText("yuyiRuntimeTag", tag);
    setClass("yuyiRuntimeTag", data.online ? "is-online" : "is-offline");
    const authList = $("yuyiRuntimeAuth");
    if (authList) {
      authList.innerHTML = "";
      const auth = rs.authority || {};
      Object.keys(auth).forEach((k) => {
        const li = document.createElement("li");
        const dot = auth[k] ? "is-online" : "is-offline";
        li.innerHTML = `<span>${k}</span><b class="${dot}">${auth[k] ? "✓" : "✗"}</b>`;
        authList.appendChild(li);
      });
    }

    // Emotion
    const e = data.emotion || {};
    setText("yuyiEmotionPrimary", e.primary || "unknown");
    setText("yuyiEmotionValence", (e.valence || 0).toFixed(2));
    setText("yuyiEmotionArousal", (e.arousal || 0).toFixed(2));
    setText("yuyiEmotionIntensity", (e.intensity || 0).toFixed(2));

    // Health
    const h = data.health || {};
    setText("yuyiHealthStatus", h.status || "unknown");
    setClass("yuyiHealthStatus", healthClass(h.status));
    setText("yuyiHealthScore", h.score || 0);
    const issues = $("yuyiHealthIssues");
    if (issues) {
      issues.innerHTML = "";
      (h.issues || []).forEach((it) => {
        const li = document.createElement("li");
        li.textContent = it;
        issues.appendChild(li);
      });
    }

    // Goal / Interest
    setText("yuyiCurrentGoal", data.current_goal ? (data.current_goal.title || JSON.stringify(data.current_goal)) : "暂无活跃目标");
    setText("yuyiCurrentInterest", data.current_interest ? (data.current_interest.title || JSON.stringify(data.current_interest)) : "暂无主动行为");

    if (fallback) {
      toast("Provider 不可用,展示 fallback 数据");
    }
  }

  function renderEvents(events) {
    const list = $("yuyiEventsList");
    if (!list) return;
    if (!events || !events.length) {
      list.innerHTML = '<li class="yuyi-events__item yuyi-events__item--empty">暂无事件</li>';
      return;
    }
    list.innerHTML = "";
    events.forEach((ev) => {
      const li = document.createElement("li");
      li.className = "yuyi-events__item";
      li.innerHTML = `
        <b>${ev.event_type || "unknown"}</b>
        <span>${ev.source || ""}</span>
        <small>${ev.received_at || ev.timestamp || ""}</small>
      `;
      list.appendChild(li);
    });
  }

  // ----------------------------------------------------------
  // 数据加载
  // ----------------------------------------------------------
  async function loadOverview() {
    try {
      const r = await API.overview();
      renderOverview(r.data || {}, r.fallback);
    } catch (e) {
      toast("加载失败: " + (e && e.message ? e.message : e));
    }
  }

  async function loadEvents() {
    try {
      const r = await API.events(20);
      renderEvents((r.data && r.data.events) || []);
    } catch (e) {
      // 静默
    }
  }

  // ----------------------------------------------------------
  // 事件订阅
  // ----------------------------------------------------------
  function bindEvents() {
    const btn = $("yuyiRefreshBtn");
    if (btn) {
      btn.addEventListener("click", () => {
        loadOverview();
        loadEvents();
        toast("已刷新");
      });
    }
  }

  function init() {
    if (!API) {
      toast("API 客户端未加载");
      return;
    }
    bindEvents();
    loadOverview();
    loadEvents();

    if (WS) {
      WS.subscribe((payload) => {
        if (payload && payload.type === "events") {
          renderEvents(payload.events || []);
        }
      });
      WS.connect();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
