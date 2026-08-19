/* =============================================================
 * selfmodel.js —— SelfModel 页面渲染
 *
 * 数据源:
 *  - /api/dashboard/v2/selfmodel/identity
 *  - /api/dashboard/v2/selfmodel/traits
 *  - /api/dashboard/v2/selfmodel/capabilities
 *  - /api/dashboard/v2/selfmodel/beliefs
 *  - /api/dashboard/v2/selfmodel/timeline
 *  - /api/dashboard/v2/selfmodel/health
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;

  function $(id) { return document.getElementById(id); }
  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = (value === undefined || value === null || value === "") ? "--" : String(value);
  }
  function setHtml(id, html) {
    const el = $(id);
    if (el) el.innerHTML = html;
  }
  function toast(msg) {
    const el = $("yuyiToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    setTimeout(() => el.classList.remove("is-visible"), 2400);
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function healthClass(status) {
    if (status === "healthy") return "is-online";
    if (status === "degraded" || status === "warning") return "is-degraded";
    return "is-offline";
  }

  async function loadIdentity() {
    if (!API || !API.selfmodelIdentity) return;
    try {
      const r = await API.selfmodelIdentity();
      const d = r.data || {};
      setText("ysm-name", d.identity_name);
      setText("ysm-source", d.source || "fallback");
      setText("ysm-version", d.version || "--");
      setText("ysm-anchor", d.anchor || "--");
      setText("ysm-id", d.identity_id || "--");
      setText("ysm-stable", d.stable ? "是" : "否");
      const values = Array.isArray(d.core_values) ? d.core_values : [];
      setHtml("ysm-values", values.length
        ? values.map((v) => `<span class="ysm-pill">${esc(v)}</span>`).join("")
        : '<span class="yuyi-empty">暂无核心价值</span>');
      const tag = $("ysm-status-tag");
      if (tag) {
        tag.textContent = d.available ? "active" : "fallback";
        tag.className = "yuyi-card__tag " + (d.available ? "is-online" : "is-offline");
      }
      if (r.fallback) toast("Identity 数据 fallback 中");
    } catch (e) {
      toast("加载 identity 失败");
    }
  }

  function renderList(targetId, items, renderItem, emptyText) {
    if (!items || !items.length) {
      setHtml(targetId, `<li class="yuyi-list__empty">${emptyText}</li>`);
      return;
    }
    setHtml(targetId, items.map(renderItem).join(""));
  }

  async function loadTraits() {
    if (!API || !API.selfmodelTraits) return;
    try {
      const r = await API.selfmodelTraits();
      const d = r.data || {};
      const items = d.items || [];
      renderList(
        "ysm-traits-list",
        items,
        (t) => `<li class="ysm-row">
          <span class="ysm-row__name">${esc(t.name || t.trait_id)}</span>
          <span class="ysm-row__meta">${esc(t.domain)} · conf=${(Number(t.confidence) || 0).toFixed(2)} · v${t.version || 1}</span>
        </li>`,
        "暂无 trait"
      );
      setText("ysm-traits-total", d.total || 0);
    } catch (e) {
      // 静默
    }
  }

  async function loadCapabilities() {
    if (!API || !API.selfmodelCapabilities) return;
    try {
      const r = await API.selfmodelCapabilities();
      const d = r.data || {};
      const items = d.items || [];
      renderList(
        "ysm-caps-list",
        items,
        (c) => `<li class="ysm-row">
          <span class="ysm-row__name">${esc(c.name || c.capability_id)}</span>
          <span class="ysm-row__meta">${esc(c.domain)} · conf=${(Number(c.confidence) || 0).toFixed(2)} · ev=${c.evidence_count || 0}</span>
        </li>`,
        "暂无 capability"
      );
      setText("ysm-caps-total", d.total || 0);
    } catch (e) {
      // 静默
    }
  }

  async function loadBeliefs() {
    if (!API || !API.selfmodelBeliefs) return;
    try {
      const r = await API.selfmodelBeliefs(20);
      const d = r.data || {};
      const items = d.items || [];
      const byDomain = d.by_domain || {};
      const domText = Object.keys(byDomain).length
        ? Object.entries(byDomain).map(([k, v]) => `${k}:${v}`).join(" · ")
        : "无";
      setText("ysm-beliefs-by-domain", domText);
      setText("ysm-beliefs-total", d.total || 0);
      setText("ysm-beliefs-active", d.active || 0);
      setText("ysm-beliefs-inactive", d.inactive || 0);
      renderList(
        "ysm-beliefs-list",
        items.slice(0, 20),
        (b) => `<li class="ysm-row">
          <span class="ysm-row__name">${esc(b.content || b.belief_id)}</span>
          <span class="ysm-row__meta">${esc(b.domain)} · conf=${(Number(b.confidence) || 0).toFixed(2)} · ${b.active ? "active" : "inactive"}</span>
        </li>`,
        "暂无 belief"
      );
    } catch (e) {
      // 静默
    }
  }

  async function loadTimeline() {
    if (!API || !API.selfmodelTimeline) return;
    try {
      const r = await API.selfmodelTimeline(20);
      const d = r.data || {};
      const items = d.items || [];
      renderList(
        "ysm-timeline-list",
        items,
        (it) => `<li class="yuyi-events__item">
          <b>${esc(it.event_type || it.type || "event")}</b>
          <span>${esc(it.summary || it.actor || "")}</span>
          <small>${esc(it.timestamp || it.event_id || "")}</small>
        </li>`,
        "暂无 timeline 事件"
      );
    } catch (e) {
      // 静默
    }
  }

  async function loadHealth() {
    if (!API || !API.selfmodelHealth) return;
    try {
      const r = await API.selfmodelHealth();
      const d = r.data || {};
      setText("ysm-health-status", d.status || "unknown");
      setText("ysm-health-score", d.score != null ? d.score : "--");
      const tag = $("ysm-health-tag");
      if (tag) {
        tag.textContent = d.status || "unknown";
        tag.className = "yuyi-card__tag " + healthClass(d.status);
      }
      const issues = Array.isArray(d.issues) ? d.issues : [];
      setHtml(
        "ysm-health-issues",
        issues.length
          ? issues.map((x) => `<li>${esc(typeof x === "string" ? x : (x.message || JSON.stringify(x)))}</li>`).join("")
          : '<li class="yuyi-list__empty">无异常</li>'
      );
    } catch (e) {
      // 静默
    }
  }

  async function refresh() {
    await Promise.all([
      loadIdentity(),
      loadTraits(),
      loadCapabilities(),
      loadBeliefs(),
      loadTimeline(),
      loadHealth(),
    ]);
  }

  function init() {
    const root = document.querySelector('[data-page-id="selfmodel"]');
    if (!root) return;
    refresh();
    const btn = document.getElementById("ysm-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
