/* ============================================
   羽依代理管理面板 — Yuyi Agents Dashboard
   集成 /admin/api/agents 与 /admin/api/agents/status
   风格参考：yuyi-cognitive.js / yuyi-autonomous.js
   ============================================ */

const YuyiAgents = (function () {
  "use strict";

  const API_BASE = "/admin/api";
  const REFRESH_INTERVAL = 15000; // 默认每 15 秒自动刷新

  // 模块内部状态
  let initialized = false;
  let autoRefreshTimer = null;
  let lastRefreshTime = null;
  let lastData = null;

  /* ============ API 封装 ============ */

  async function fetchAgents() {
    try {
      const res = await fetch(`${API_BASE}/agents`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      console.error("[Agents] /agents failed:", e);
      return null;
    }
  }

  async function fetchStatus() {
    try {
      const res = await fetch(`${API_BASE}/agents/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      console.error("[Agents] /agents/status failed:", e);
      return null;
    }
  }

  /* ============ 工具函数 ============ */

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    const div = document.createElement("div");
    div.textContent = String(str);
    return div.innerHTML;
  }

  // 把时间戳/字符串格式化为可读的本地时间
  function formatTime(ts) {
    if (!ts) return "--";
    let d;
    if (typeof ts === "number") {
      // 兼容秒级与毫秒级时间戳
      d = ts < 1e12 ? new Date(ts * 1000) : new Date(ts);
    } else {
      d = new Date(ts);
    }
    if (isNaN(d.getTime())) return escapeHtml(ts);
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  // 计算从 last_heartbeat 到现在的相对时间描述
  function relativeTime(ts) {
    if (!ts) return "未知";
    let d;
    if (typeof ts === "number") {
      d = ts < 1e12 ? new Date(ts * 1000) : new Date(ts);
    } else {
      d = new Date(ts);
    }
    if (isNaN(d.getTime())) return escapeHtml(ts);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 5) return "刚刚";
    if (diff < 60) return `${Math.floor(diff)} 秒前`;
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return `${Math.floor(diff / 86400)} 天前`;
  }

  /* ============ 初始化 ============ */

  function init() {
    if (initialized) return true;

    const refreshBtn = document.getElementById("agents-refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        load();
        if (typeof YuyiAudio !== "undefined") {
          try { YuyiAudio.play("sparkle"); } catch (_) {}
        }
      });
    }

    const autoCheckbox = document.getElementById("agents-auto-refresh");
    if (autoCheckbox) {
      autoCheckbox.addEventListener("change", (e) => {
        if (e.target.checked) {
          startAutoRefresh();
          if (typeof showToast === "function") {
            showToast("已开启自动刷新", "info");
          }
        } else {
          stopAutoRefresh();
          if (typeof showToast === "function") {
            showToast("已关闭自动刷新", "info");
          }
        }
      });
    }

    initialized = true;
    return true;
  }

  /* ============ 自动刷新 ============ */

  function startAutoRefresh() {
    stopAutoRefresh();
    autoRefreshTimer = setInterval(() => {
      // 仅当代理视图可见时才真正发请求，避免后台空跑
      const view = document.getElementById("agents-section");
      if (view && view.style.display !== "none") {
        load(true);
      }
    }, REFRESH_INTERVAL);
  }

  function stopAutoRefresh() {
    if (autoRefreshTimer) {
      clearInterval(autoRefreshTimer);
      autoRefreshTimer = null;
    }
  }

  /* ============ 主加载器 ============ */

  async function load(silent = false) {
    // 同时拉取两个接口，状态接口失败时回退到 agents 接口
    const [agentsData, statusData] = await Promise.all([
      fetchAgents(),
      fetchStatus(),
    ]);

    // 优先使用 status 接口的数据，因为它包含更完整的服务状态
    const merged = mergeStatus(agentsData, statusData);
    lastData = merged;
    lastRefreshTime = new Date();

    renderStatus(merged);
    renderAgents(merged.agents || []);

    updateFooter(merged, silent);
  }

  // 合并两个接口的返回，优先取 status 接口
  function mergeStatus(agentsData, statusData) {
    if (statusData && statusData.enabled !== undefined) {
      return {
        enabled: !!statusData.enabled,
        running: !!statusData.running,
        total_agents: statusData.total_agents || (statusData.agents || []).length,
        authenticated_agents:
          statusData.authenticated_agents !== undefined
            ? statusData.authenticated_agents
            : (statusData.agents || []).filter((a) => a.authenticated).length,
        agents: statusData.agents || agentsData?.agents || [],
      };
    }
    if (agentsData) {
      const agents = agentsData.agents || [];
      return {
        enabled: !!agentsData.enabled,
        running: !!agentsData.enabled, // 没有 running 字段时，启用即视为运行
        total_agents: agentsData.total || agents.length,
        authenticated_agents: agents.filter((a) => a.authenticated).length,
        agents: agents,
      };
    }
    return {
      enabled: false,
      running: false,
      total_agents: 0,
      authenticated_agents: 0,
      agents: [],
    };
  }

  /* ============ 渲染：顶部状态卡 ============ */

  function renderStatus(data) {
    const setText = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };

    setText("agents-stat-enabled", data.enabled ? "已启用" : "未启用");
    setText("agents-stat-running", data.running ? "运行中" : "已停止");
    setText("agents-stat-total", data.total_agents || 0);
    setText("agents-stat-auth", data.authenticated_agents || 0);

    // 根据状态切换卡片的高亮态
    toggleStatCardState("agents-stat-enabled", data.enabled);
    toggleStatCardState("agents-stat-running", data.running);
  }

  // 给状态卡加上/移除 active 类，用于颜色变化
  function toggleStatCardState(valueId, positive) {
    const el = document.getElementById(valueId);
    if (!el) return;
    const card = el.closest(".agents-stat-card");
    if (card) {
      card.classList.toggle("is-positive", !!positive);
      card.classList.toggle("is-muted", !positive);
    }
  }

  /* ============ 渲染：代理列表 ============ */

  function renderAgents(agents) {
    const container = document.getElementById("agents-list");
    if (!container) return;

    if (!agents || agents.length === 0) {
      container.innerHTML = renderEmpty();
      return;
    }

    container.innerHTML = agents.map(renderAgentCard).join("");
  }

  function renderAgentCard(agent) {
    const authenticated = !!agent.authenticated;
    const authBadge = authenticated
      ? '<span class="agents-badge agents-badge-auth">已认证</span>'
      : '<span class="agents-badge agents-badge-unauth">未认证</span>';

    // 头像首字母：取 agent_id 首字符
    const initial = (agent.agent_id || "?").charAt(0).toUpperCase();

    return `
      <div class="agent-card ${authenticated ? "is-auth" : "is-unauth"}">
        <div class="agent-card-header">
          <div class="agent-avatar">${escapeHtml(initial)}</div>
          <div class="agent-meta">
            <div class="agent-id">${escapeHtml(agent.agent_id || "未知代理")}</div>
            <div class="agent-sub">
              <span class="agent-user" title="用户 ID">
                <svg viewBox="0 0 24 24" width="11" height="11"><circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
                ${escapeHtml(agent.user_id || "匿名")}
              </span>
              ${authBadge}
            </div>
          </div>
        </div>
        <div class="agent-card-body">
          <div class="agent-info-row">
            <span class="agent-info-label">主机名</span>
            <span class="agent-info-value">${escapeHtml(agent.hostname || "--")}</span>
          </div>
          <div class="agent-info-row">
            <span class="agent-info-label">系统</span>
            <span class="agent-info-value agent-info-system">${escapeHtml(agent.system || "--")}</span>
          </div>
          <div class="agent-info-row">
            <span class="agent-info-label">连接时间</span>
            <span class="agent-info-value">${formatTime(agent.connected_at)}</span>
          </div>
          <div class="agent-info-row">
            <span class="agent-info-label">最后心跳</span>
            <span class="agent-info-value">
              ${relativeTime(agent.last_heartbeat)}
              <span class="agent-heartbeat-dot" title="心跳活跃"></span>
            </span>
          </div>
        </div>
      </div>
    `;
  }

  /* ============ 空状态 ============ */

  function renderEmpty() {
    return `
      <div class="agents-empty">
        <div class="agents-empty-icon">✦</div>
        <div class="agents-empty-text">羽依正在等待你的电脑连接...</div>
        <div class="agents-empty-hint">
          启动本地代理客户端后，这里会出现在线设备
        </div>
      </div>
    `;
  }

  /* ============ 底部刷新信息 ============ */

  function updateFooter(data, silent) {
    const footer = document.getElementById("agents-footer");
    if (!footer) return;
    const time = lastRefreshTime
      ? lastRefreshTime.toLocaleTimeString("zh-CN")
      : "--:--:--";
    const total = data.total_agents || 0;
    footer.innerHTML = `
      <span class="agents-footer-text">
        最后刷新：${time} · 共 ${total} 个代理在线
      </span>
    `;
    if (!silent && typeof showToast === "function") {
      showToast("代理列表已刷新", "success");
    }
  }

  /* ============ 对外接口 ============ */

  return {
    init,
    load,
    startAutoRefresh,
    stopAutoRefresh,
    renderStatus,
    renderAgents,
  };
})();
