/* =============================================================
 * audit.js —— Audit Viewer
 *
 * 职责:
 *  - 展示 POST 审计日志(只读)
 *  - 不创建事件、不修改业务
 *  - 默认展示最近 50 条
 *  - 支持按 who / action / result 过滤
 *
 * 数据源: GET /api/dashboard/v2/audit/logs
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;

  function $(id) { return document.getElementById(id); }
  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = (value === undefined || value === null) ? "--" : String(value);
  }
  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function toast(msg) {
    const el = $("yuyiToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    setTimeout(() => el.classList.remove("is-visible"), 2400);
  }

  function resultClass(r) {
    if (r === "success") return "is-online";
    if (r === "failure") return "is-critical";
    if (r === "denied") return "is-degraded";
    return "is-offline";
  }

  function formatTs(ts) {
    if (!ts) return "--";
    try {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return String(ts);
      const pad = (n) => String(n).padStart(2, "0");
      return (
        d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
        " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
      );
    } catch (e) { return String(ts); }
  }

  async function loadAudit() {
    if (!API || !API.auditLogs) {
      toast("API 客户端缺少 auditLogs");
      return;
    }
    const list = $("yaudit-list");
    const tag = $("yaudit-status");
    if (tag) {
      tag.textContent = "loading";
      tag.className = "yuyi-card__tag is-degraded";
    }
    if (list) {
      list.innerHTML = '<li class="yuyi-list__empty">加载中…</li>';
    }
    try {
      const who = ($("yaudit-filter-who") && $("yaudit-filter-who").value || "").trim();
      const action = ($("yaudit-filter-action") && $("yaudit-filter-action").value || "").trim();
      const result = ($("yaudit-filter-result") && $("yaudit-filter-result").value || "").trim();

      const env = await API.auditLogs({
        limit: 100,
        who: who || undefined,
        action: action || undefined,
        result: result || undefined,
      });

      const isFallback = env && env.fallback === true;
      const d = (env && env.data) || {};
      const items = Array.isArray(d.items) ? d.items : [];
      const total = typeof d.count === "number" ? d.count : items.length;

      if (tag) {
        if (isFallback) {
          tag.textContent = "fallback";
          tag.className = "yuyi-card__tag is-offline";
        } else {
          tag.textContent = "🟢 " + total + " 条";
          tag.className = "yuyi-card__tag is-online";
        }
      }

      // stats
      let successN = 0, deniedN = 0, failureN = 0;
      for (const it of items) {
        if (it.result === "success") successN++;
        else if (it.result === "denied") deniedN++;
        else if (it.result === "failure") failureN++;
      }
      setText("yaudit-stat-success", successN);
      setText("yaudit-stat-denied", deniedN);
      setText("yaudit-stat-failure", failureN);
      setText("yaudit-stat-total", total);

      // render list
      if (!list) return;
      if (isFallback) {
        list.innerHTML = '<li class="yuyi-list__empty">审计日志不可用 (fallback)</li>';
        return;
      }
      if (!items.length) {
        list.innerHTML = '<li class="yuyi-list__empty">暂无审计记录</li>';
        return;
      }
      list.innerHTML = "";
      for (const it of items) {
        const li = document.createElement("li");
        li.className = "yuyi-audit__item";
        const r = it.result || "success";
        const cls = resultClass(r);
        li.innerHTML = `
          <div class="yuyi-audit__row">
            <b class="yuyi-audit__who">${escapeHtml(it.who || it.role || "unknown")}</b>
            <span class="yuyi-audit__action">${escapeHtml(it.action || "--")}</span>
            <span class="yuyi-audit__tag ${cls}">${escapeHtml(r)}</span>
          </div>
          <div class="yuyi-audit__meta">
            <span>${escapeHtml(formatTs(it.timestamp))}</span>
            <span class="yuyi-audit__reason">${escapeHtml(it.reason || "")}</span>
          </div>
        `;
        list.appendChild(li);
      }
    } catch (e) {
      if (tag) {
        tag.textContent = "error";
        tag.className = "yuyi-card__tag is-offline";
      }
      if (list) {
        list.innerHTML = '<li class="yuyi-list__empty">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</li>';
      }
    }
  }

  function bindEvents() {
    const refresh = $("yaudit-refresh");
    if (refresh) refresh.addEventListener("click", () => loadAudit());
    const apply = $("yaudit-apply");
    if (apply) apply.addEventListener("click", () => loadAudit());
  }

  function init() {
    // 该卡片可放在任意位置;只有存在 data-block="audit-viewer" 才初始化
    const root = document.querySelector('[data-block="audit-viewer"]');
    if (!root) return;
    bindEvents();
    loadAudit();
    // 30s 自动刷新
    setInterval(() => {
      if (document.querySelector('[data-block="audit-viewer"]')) {
        loadAudit();
      }
    }, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
