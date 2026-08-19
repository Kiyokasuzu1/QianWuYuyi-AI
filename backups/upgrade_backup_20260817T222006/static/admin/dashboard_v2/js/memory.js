/* =============================================================
 * memory.js —— Memory 页面渲染
 *
 * 数据源:
 *  - /api/dashboard/v2/memory/summary
 *  - /api/dashboard/v2/memory/recent?limit=20
 *  - /api/dashboard/v2/memory/important?limit=20
 *  - /api/dashboard/v2/memory/timeline?range=7d
 *  - /api/dashboard/v2/memory/<id>
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
  function esc(s) {
    return String(s == null ? "" : s)
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
  function fmtTime(ts) {
    if (!ts) return "--";
    try {
      return String(ts).replace("T", " ").replace("Z", "").slice(0, 19);
    } catch (e) {
      return String(ts);
    }
  }
  function importanceClass(imp) {
    const v = Number(imp) || 0;
    if (v >= 0.8) return "is-online";
    if (v >= 0.5) return "is-degraded";
    return "is-offline";
  }
  function importanceLabel(imp) {
    const v = Number(imp) || 0;
    return v.toFixed(2);
  }

  async function loadSummary() {
    if (!API || !API.memorySummary) return;
    try {
      const r = await API.memorySummary();
      const d = r.data || {};
      setText("ymm-stat-total", d.total_count || 0);
      setText("ymm-stat-important", d.important_count || 0);
      setText("ymm-stat-recent", d.recent_count || 0);
      setText("ymm-last-update", d.last_update ? fmtTime(d.last_update) : "无");
      if (r.fallback) toast("Memory Summary 不可用");
    } catch (e) {
      // 静默
    }
  }

  async function loadRecent() {
    if (!API || !API.memoryRecent) return;
    try {
      const r = await API.memoryRecent(20);
      const d = r.data || {};
      const items = d.items || [];
      renderMemoryList("ymm-recent-list", items, "暂无近期记忆");
    } catch (e) {
      // 静默
    }
  }

  async function loadImportant() {
    if (!API || !API.memoryImportant) return;
    try {
      const r = await API.memoryImportant(20);
      const d = r.data || {};
      const items = d.items || [];
      renderMemoryList("ymm-important-list", items, "暂无重要记忆");
    } catch (e) {
      // 静默
    }
  }

  async function loadTimeline() {
    if (!API || !API.memoryTimeline) return;
    try {
      const range = ($("ymm-range") && $("ymm-range").value) || "7d";
      const r = await API.memoryTimeline(range);
      const d = r.data || {};
      const buckets = d.buckets || [];
      const total = d.total || 0;
      setText("ymm-timeline-total", total);
      setText("ymm-timeline-range", d.range || range);
      if (!buckets.length) {
        setHtml("ymm-timeline-list", '<li class="yuyi-list__empty">该范围内暂无记忆</li>');
        return;
      }
      // 渲染柱状条(以最大 count 为基线)
      const max = Math.max(...buckets.map((b) => Number(b.count) || 0), 1);
      setHtml(
        "ymm-timeline-list",
        buckets.map((b) => {
          const w = Math.round((Number(b.count) || 0) / max * 100);
          return `<li class="ymm-timeline__row">
            <span class="ymm-timeline__date">${esc(b.date)}</span>
            <div class="ymm-timeline__bar"><span style="width:${w}%"></span></div>
            <span class="ymm-timeline__count">${b.count} / 重要 ${b.important_count || 0}</span>
          </li>`;
        }).join("")
      );
    } catch (e) {
      // 静默
    }
  }

  function renderMemoryList(targetId, items, emptyText) {
    if (!items || !items.length) {
      setHtml(targetId, `<li class="yuyi-list__empty">${emptyText}</li>`);
      return;
    }
    setHtml(
      targetId,
      items.map((m) => {
        const id = esc(m.id);
        return `<li class="ymm-row" data-memory-id="${id}">
          <div class="ymm-row__main">
            <span class="ymm-row__summary">${esc(m.summary || "(无内容)")}</span>
            <small class="ymm-row__meta">${esc(m.memory_type || "general")} · ${fmtTime(m.created_at)}</small>
          </div>
          <span class="ymm-row__imp ${importanceClass(m.importance)}">${importanceLabel(m.importance)}</span>
        </li>`;
      }).join("")
    );
    // 绑定点击事件(查看详情)
    document.querySelectorAll(`#${targetId} .ymm-row`).forEach((el) => {
      el.addEventListener("click", () => {
        const mid = el.getAttribute("data-memory-id");
        if (mid) loadDetail(mid);
      });
    });
  }

  async function loadDetail(memoryId) {
    if (!API || !API.memoryDetail) return;
    const panel = $("ymm-detail");
    if (!panel) return;
    setHtml("ymm-detail", '<p class="yuyi-empty">加载中…</p>');
    try {
      const r = await API.memoryDetail(memoryId);
      const d = r.data || {};
      const m = d.memory;
      if (!m) {
        setHtml("ymm-detail", '<p class="yuyi-empty">未找到该记忆</p>');
        return;
      }
      setHtml(
        "ymm-detail",
        `<dl class="yuyi-stat">
          <div><dt>id</dt><dd>${esc(m.id)}</dd></div>
          <div><dt>type</dt><dd>${esc(m.memory_type)}</dd></div>
          <div><dt>importance</dt><dd>${importanceLabel(m.importance)}</dd></div>
          <div><dt>created_at</dt><dd>${esc(fmtTime(m.created_at))}</dd></div>
        </dl>
        <div class="ymm-detail__content">
          <h4>内容</h4>
          <p>${esc(m.content || m.summary || "(无)")}</p>
        </div>`
      );
    } catch (e) {
      setHtml("ymm-detail", '<p class="yuyi-empty">加载失败</p>');
    }
  }

  async function refresh() {
    await Promise.all([loadSummary(), loadRecent(), loadImportant(), loadTimeline()]);
  }

  function init() {
    const root = document.querySelector('[data-page-id="memory"]');
    if (!root) return;
    refresh();
    const btn = document.getElementById("ymm-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
    const rangeSel = document.getElementById("ymm-range");
    if (rangeSel) rangeSel.addEventListener("change", () => loadTimeline());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
