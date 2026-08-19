/* =============================================================
 * reflection.js —— Reflection 页面渲染
 *
 * 数据源:
 *  - /api/dashboard/v2/reflection/summary
 *  - /api/dashboard/v2/reflection/list?limit=20&type=daily|event|growth
 *  - /api/dashboard/v2/reflection/<id>
 *  - /api/dashboard/v2/reflection/<id>/insights
 *  - /api/dashboard/v2/reflection/<id>/evidence
 *
 * 布局:
 *  - 顶部:统计(Total / Daily / Event / Growth)
 *  - 左:Timeline
 *  - 中:Detail
 *  - 右:Evidence Chain(Memory → Event → Insight)
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;

  // ---- DOM helpers ----
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
  function typeLabel(t) {
    if (t === "daily") return "每日";
    if (t === "event") return "事件";
    if (t === "growth") return "成长";
    return t || "--";
  }
  function typeClass(t) {
    if (t === "daily") return "yrf-type--daily";
    if (t === "event") return "yrf-type--event";
    if (t === "growth") return "yrf-type--growth";
    return "";
  }

  // ---- State ----
  let currentReflectionId = "";
  let currentType = "";

  // ---- Summary ----
  async function loadSummary() {
    if (!API || !API.reflectionSummary) return;
    try {
      const r = await API.reflectionSummary();
      const d = r.data || {};
      setText("yrf-stat-total", d.total_count || 0);
      const byType = d.by_type || {};
      setText("yrf-stat-daily", byType.daily || 0);
      setText("yrf-stat-event", byType.event || 0);
      setText("yrf-stat-growth", byType.growth || 0);
      setText("yrf-last-id", d.last_reflection_id || "--");
      setText("yrf-last-at", d.last_reflection_at ? fmtTime(d.last_reflection_at) : "--");
      if (r.fallback) toast("Reflection Summary 不可用");
    } catch (e) {
      // 静默
    }
  }

  // ---- Timeline / List ----
  async function loadList() {
    if (!API || !API.reflectionList) return;
    try {
      const sel = $("yrf-type-filter");
      const t = (sel && sel.value) || "";
      currentType = t || "";
      const r = await API.reflectionList(50, currentType || null);
      const d = r.data || {};
      const items = d.items || [];
      renderList(items);
    } catch (e) {
      // 静默
    }
  }

  function renderList(items) {
    const targetId = "yrf-timeline-list";
    if (!items || !items.length) {
      setHtml(targetId, '<li class="yuyi-list__empty">暂无 Reflection 记录</li>');
      return;
    }
    setHtml(
      targetId,
      items.map((it) => {
        const id = esc(it.reflection_id || "");
        const t = esc(it.type || "daily");
        const cls = typeClass(t);
        const label = typeLabel(t);
        const summary = it.summary || "(无摘要)";
        return `<li class="yrf-row" data-reflection-id="${id}">
          <div class="yrf-row__main">
            <span class="yrf-row__summary">${esc(summary)}</span>
            <small class="yrf-row__meta">
              <span class="yrf-type ${cls}">${esc(label)}</span>
              · ${esc(fmtTime(it.created_at))}
              · insights ${it.insight_count || 0} / conf ${(Number(it.confidence) || 0).toFixed(2)}
            </small>
          </div>
        </li>`;
      }).join("")
    );
    // 点击查看详情
    document.querySelectorAll(`#${targetId} .yrf-row`).forEach((el) => {
      el.addEventListener("click", () => {
        const rid = el.getAttribute("data-reflection-id");
        if (rid) {
          currentReflectionId = rid;
          loadDetail(rid);
          loadInsights(rid);
          loadEvidence(rid);
          highlight(rid);
        }
      });
    });
  }

  function highlight(rid) {
    document.querySelectorAll("#yrf-timeline-list .yrf-row").forEach((el) => {
      el.classList.toggle("is-active", el.getAttribute("data-reflection-id") === rid);
    });
  }

  // ---- Detail ----
  async function loadDetail(rid) {
    if (!API || !API.reflectionDetail) return;
    const panel = $("yrf-detail");
    if (!panel) return;
    setHtml("yrf-detail", '<p class="yuyi-empty">加载中…</p>');
    try {
      const r = await API.reflectionDetail(rid);
      const d = r.data || {};
      const refl = d.reflection;
      if (!refl) {
        setHtml("yrf-detail", '<p class="yuyi-empty">未找到该 Reflection</p>');
        return;
      }
      const t = refl.type || "daily";
      const cls = typeClass(t);
      setHtml(
        "yrf-detail",
        `<div class="yrf-detail__head">
          <span class="yrf-type ${cls}">${esc(typeLabel(t))}</span>
          <span class="yrf-detail__id">${esc(refl.reflection_id || "")}</span>
        </div>
        <dl class="yuyi-stat">
          <div><dt>created_at</dt><dd>${esc(fmtTime(refl.created_at))}</dd></div>
          <div><dt>insight_count</dt><dd>${refl.insight_count || 0}</dd></div>
          <div><dt>suggestion_count</dt><dd>${refl.suggestion_count || 0}</dd></div>
          <div><dt>confidence</dt><dd>${(Number(refl.confidence) || 0).toFixed(2)}</dd></div>
          <div><dt>evidence_strength</dt><dd>${(Number(refl.evidence_strength) || 0).toFixed(2)}</dd></div>
          <div><dt>source_events</dt><dd>${refl.source_event_count || (refl.source_events ? refl.source_events.length : 0)}</dd></div>
        </dl>
        <div class="yrf-detail__section">
          <h4>Summary</h4>
          <p>${esc(refl.summary || "(无摘要)")}</p>
        </div>
        ${refl.content ? `<div class="yrf-detail__section">
          <h4>Content</h4>
          <p>${esc(refl.content)}</p>
        </div>` : ""}
        ${refl.source_events && refl.source_events.length ? `<div class="yrf-detail__section">
          <h4>Source Events</h4>
          <ul class="yrf-tag-list">${refl.source_events.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
        </div>` : ""}`
      );
    } catch (e) {
      setHtml("yrf-detail", '<p class="yuyi-empty">加载失败</p>');
    }
  }

  // ---- Insights ----
  async function loadInsights(rid) {
    if (!API || !API.reflectionInsights) return;
    const panel = $("yrf-insights");
    if (!panel) return;
    setHtml("yrf-insights", '<p class="yuyi-empty">加载中…</p>');
    try {
      const r = await API.reflectionInsights(rid);
      const d = r.data || {};
      const items = d.items || [];
      const summary = d.summary || {};
      if (items && items.length) {
        setHtml(
          "yrf-insights",
          `<ul class="yrf-insight-list">
            ${items.map((it) => `<li class="yrf-insight">
              <span class="yrf-insight__cat">${esc(it.category || "pattern")}</span>
              <span class="yrf-insight__desc">${esc(it.description || "")}</span>
              <small class="yrf-insight__conf">conf ${(Number(it.confidence) || 0).toFixed(2)} · evidence ${it.evidence_count || 0}</small>
            </li>`).join("")}
          </ul>
          <p class="yuyi-empty">${items.length} 条 Insight(详情)</p>`
        );
      } else {
        setHtml(
          "yrf-insights",
          `<p class="yuyi-empty">Insight 详情不可用(Provider 仅返回摘要)</p>
          <dl class="yuyi-stat">
            <div><dt>insight_count</dt><dd>${summary.insight_count || 0}</dd></div>
            <div><dt>suggestion_count</dt><dd>${summary.suggestion_count || 0}</dd></div>
            <div><dt>confidence</dt><dd>${(Number(summary.confidence) || 0).toFixed(2)}</dd></div>
            <div><dt>evidence_strength</dt><dd>${(Number(summary.evidence_strength) || 0).toFixed(2)}</dd></div>
          </dl>`
        );
      }
    } catch (e) {
      setHtml("yrf-insights", '<p class="yuyi-empty">加载失败</p>');
    }
  }

  // ---- Evidence Chain ----
  async function loadEvidence(rid) {
    if (!API || !API.reflectionEvidence) return;
    const panel = $("yrf-evidence");
    if (!panel) return;
    setHtml("yrf-evidence", '<p class="yuyi-empty">加载中…</p>');
    try {
      const r = await API.reflectionEvidence(rid);
      const d = r.data || {};
      const chain = d.chain || {};
      const events = chain.events || [];
      const memories = chain.memories || [];
      const evidence = chain.evidence || [];
      const reflection = chain.reflection;

      if (!reflection) {
        setHtml("yrf-evidence", '<p class="yuyi-empty">未找到该 Reflection 的证据链</p>');
        return;
      }

      // 渲染:Reflection → Memory → Event → Evidence
      const memHtml = memories.length
        ? memories.map((m) => `<li class="yrf-node yrf-node--memory">
            <span class="yrf-node__kind">Memory</span>
            <span class="yrf-node__id">${esc(m.memory_id || m.id || "")}</span>
            <small>${esc(m.summary || "(无摘要)")}</small>
          </li>`).join("")
        : '<li class="yuyi-list__empty">无关联 Memory</li>';

      const evHtml = events.length
        ? events.map((e) => `<li class="yrf-node yrf-node--event">
            <span class="yrf-node__kind">Event</span>
            <span class="yrf-node__id">${esc(e.event_id || e.id || "")}</span>
            <small>${esc(e.event_type || "")}</small>
          </li>`).join("")
        : '<li class="yuyi-list__empty">无关联 Event</li>';

      const eviHtml = evidence.length
        ? evidence.map((e) => `<li class="yrf-node yrf-node--evidence">
            <span class="yrf-node__kind">Evidence</span>
            <span class="yrf-node__id">${esc(e.evidence_id || e.event_id || "")}</span>
            <small>${esc(e.event_id ? "→ " + e.event_id : "")}</small>
          </li>`).join("")
        : '<li class="yuyi-list__empty">无 Evidence</li>';

      setHtml(
        "yrf-evidence",
        `<div class="yrf-chain">
          <div class="yrf-chain__level yrf-chain__level--reflection">
            <h4>Reflection</h4>
            <p class="yrf-chain__refl">${esc(reflection.summary || reflection.reflection_id || "")}</p>
          </div>
          <div class="yrf-chain__arrow">↓</div>
          <div class="yrf-chain__level yrf-chain__level--memories">
            <h4>Memory</h4>
            <ul class="yrf-node-list">${memHtml}</ul>
          </div>
          <div class="yrf-chain__arrow">↓</div>
          <div class="yrf-chain__level yrf-chain__level--events">
            <h4>Event</h4>
            <ul class="yrf-node-list">${evHtml}</ul>
          </div>
          <div class="yrf-chain__arrow">↓</div>
          <div class="yrf-chain__level yrf-chain__level--evidence">
            <h4>Evidence</h4>
            <ul class="yrf-node-list">${eviHtml}</ul>
          </div>
        </div>`
      );
    } catch (e) {
      setHtml("yrf-evidence", '<p class="yuyi-empty">加载失败</p>');
    }
  }

  // ---- Refresh ----
  async function refresh() {
    await Promise.all([loadSummary(), loadList()]);
  }

  function init() {
    const root = document.querySelector('[data-page-id="reflection"]');
    if (!root) return;
    refresh();
    const btn = $("yrf-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
    const sel = $("yrf-type-filter");
    if (sel) sel.addEventListener("change", () => loadList());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
