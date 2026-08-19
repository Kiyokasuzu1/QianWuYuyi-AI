/* =============================================================
 * life_timeline.js —— Step 8.4.3 Life Timeline (7 泳道成长时间线)
 *
 * 职责:
 *  - 渲染七泳道成长时间线 (Memory / Reflection / Interest / Action / Goal / Belief / TraitChange)
 *  - 时间范围选择 (24h / 7d / 30d / 90d / ALL)
 *  - 节点点击 → Detail Panel
 *  - 因果连线 (Memory → Reflection → Interest → Goal)
 *  - Milestone 标记 (⭐ Growth Point) + 跳转 Why API
 *
 * 数据源:
 *  GET /api/dashboard/v2/life-timeline?range=...
 *  GET /api/dashboard/v2/life-timeline/milestones
 *  GET /api/dashboard/v2/life-timeline/causality
 *  GET /api/dashboard/v2/life-graph/why/<node_id>  (跳转)
 *
 * 风格:沿用 Yuyi 玻璃拟态 + 柔色,无第三方库
 * ============================================================= */
(function (global) {
  "use strict";

  // ---- 配置 ----
  const RANGES = [
    { id: "24h", label: "24h" },
    { id: "7d", label: "7d" },
    { id: "30d", label: "30d" },
    { id: "90d", label: "90d" },
    { id: "all", label: "ALL" },
  ];

  // ---- 7 泳道视觉定义 ----
  const LANE_DEFS = [
    { id: "memory",      label: "Memory · 记忆",      color: "#bde0fe", text: "#2b4c7e" },
    { id: "reflection",  label: "Reflection · 反思",  color: "#cdb4db", text: "#4a3473" },
    { id: "interest",    label: "Interest · 兴趣",    color: "#ffc8dd", text: "#7e3b58" },
    { id: "action",      label: "Action · 行动",      color: "#ffd6a5", text: "#7e5320" },
    { id: "goal",        label: "Goal · 目标",        color: "#caffbf", text: "#2e6a36" },
    { id: "belief",      label: "Belief · 信念",      color: "#a0c4ff", text: "#2a4a8a" },
    { id: "trait_change",label: "Trait · 特质变化",   color: "#bdb2ff", text: "#3e3489" },
  ];

  const TYPE_TO_LANE = {
    memory: "memory",
    reflection: "reflection",
    interest: "interest",
    action: "action",
    goal: "goal",
    belief: "belief",
    trait_change: "trait_change",
  };

  // ---- 模块状态 ----
  let currentRange = "all";
  let currentData = null;        // get_timeline() 的 data 字段
  let currentMilestones = [];    // get_milestones() 的 milestones
  let hoverNodeId = null;
  let selectedNodeId = null;

  // ---- 工具 ----
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtTs(iso) {
    if (!iso) return "--";
    try {
      const s = String(iso);
      return s.length >= 16 ? s.slice(5, 16).replace("T", " ") : s;
    } catch (e) { return String(iso); }
  }
  function laneDef(type) {
    for (let i = 0; i < LANE_DEFS.length; i++) {
      if (LANE_DEFS[i].id === type) return LANE_DEFS[i];
    }
    return { id: type, label: type, color: "#e2e2f0", text: "#3a3a52" };
  }

  // ---- 入口 ----
  async function initLifeTimeline() {
    try {
      const root = $("yuyi-life-timeline-root");
      if (!root) return;
      renderShell(root);
      bindEvents();
      await load();
    } catch (e) {
      console.warn("life_timeline.init 异常:", e);
    }
  }

  function renderShell(root) {
    root.innerHTML = [
      '<div class="yuyi-life-timeline">',
      '  <div class="yuyi-lt-header">',
      '    <h3 class="yuyi-lt-title">🪶 Life Timeline · 成长历史</h3>',
      '    <div class="yuyi-lt-ranges" id="yuyi-lt-ranges">',
      RANGES.map(r => '<button type="button" class="yuyi-lt-range-btn" data-range="' + esc(r.id) + '">' + esc(r.label) + '</button>').join(""),
      '    </div>',
      '  </div>',
      '  <div class="yuyi-lt-summary" id="yuyi-lt-summary"></div>',
      '  <div class="yuyi-lt-lanes-wrap" id="yuyi-lt-lanes-wrap">',
      '    <div class="yuyi-lt-lanes" id="yuyi-lt-lanes"></div>',
      '    <div class="yuyi-lt-svg-wrap"><svg class="yuyi-lt-svg" id="yuyi-lt-svg"></svg></div>',
      '  </div>',
      '  <div class="yuyi-lt-detail" id="yuyi-lt-detail" style="display:none;"></div>',
      '  <div class="yuyi-lt-milestones" id="yuyi-lt-milestones"></div>',
      '</div>'
    ].join("\n");
    // 高亮默认 range
    Array.from(root.querySelectorAll(".yuyi-lt-range-btn")).forEach(b => {
      if (b.getAttribute("data-range") === currentRange) b.classList.add("active");
    });
  }

  function bindEvents() {
    document.querySelectorAll(".yuyi-lt-range-btn").forEach(btn => {
      btn.addEventListener("click", async function () {
        const r = this.getAttribute("data-range") || "all";
        if (r === currentRange) return;
        currentRange = r;
        document.querySelectorAll(".yuyi-lt-range-btn").forEach(b => b.classList.remove("active"));
        this.classList.add("active");
        await load();
      });
    });
  }

  // ---- 数据加载 ----
  async function load() {
    const summary = $("yuyi-lt-summary");
    if (summary) summary.innerHTML = '<p class="yuyi-empty">加载中…</p>';
    if (!global.YuyiDashboardAPI) {
      if (summary) summary.innerHTML = '<p class="yuyi-empty">API 不可用</p>';
      return;
    }
    try {
      const env = await global.YuyiDashboardAPI.lifeTimeline(currentRange);
      const d = env && env.data ? env.data : {};
      currentData = d;
      const ms = await global.YuyiDashboardAPI.lifeTimelineMilestones(50);
      const md = ms && ms.data ? ms.data : {};
      currentMilestones = md.milestones || [];
      renderSummary(env);
      renderLanes();
      renderMilestones();
    } catch (e) {
      console.warn("life_timeline.load 异常:", e);
      if (summary) summary.innerHTML = '<p class="yuyi-empty">加载失败: ' + esc(e && e.message ? e.message : e) + '</p>';
    }
  }

  function renderSummary(env) {
    const sum = $("yuyi-lt-summary");
    if (!sum) return;
    if (!env || env.ok !== true) {
      sum.innerHTML = '<p class="yuyi-empty">时间线不可用(fallback=' + esc(String(env && env.ok)) + ')</p>';
      return;
    }
    if (env.fallback) {
      sum.innerHTML = '<p class="yuyi-empty">时间线降级: ' + esc(env.fallback_reason || "unknown") + '</p>';
      return;
    }
    const d = currentData || {};
    const range = d.range || {};
    const counts = d.counts || {};
    const totalItems = Object.values(counts).reduce((a, b) => a + (Number(b) || 0), 0);
    const msCount = (d.milestones || []).length;
    const edgesCount = (d.edges || []).length;
    sum.innerHTML = [
      '<div class="yuyi-lt-summary-inner">',
      '  <div class="yuyi-lt-stat"><span class="yuyi-lt-stat-num">' + totalItems + '</span><span class="yuyi-lt-stat-label">节点</span></div>',
      '  <div class="yuyi-lt-stat"><span class="yuyi-lt-stat-num">' + msCount + '</span><span class="yuyi-lt-stat-label">里程碑</span></div>',
      '  <div class="yuyi-lt-stat"><span class="yuyi-lt-stat-num">' + edgesCount + '</span><span class="yuyi-lt-stat-label">因果</span></div>',
      '  <div class="yuyi-lt-range-info">',
      '    <span>区间: ' + esc(range.key || "all") + '</span>',
      '    <span>起点: ' + esc(range.start || "--") + '</span>',
      '    <span>终点: ' + esc(range.end || "--") + '</span>',
      '  </div>',
      '</div>'
    ].join("");
  }

  function renderLanes() {
    const root = $("yuyi-lt-lanes");
    if (!root) return;
    const d = currentData || {};
    const lanes = d.lanes || [];
    root.innerHTML = LANE_DEFS.map(def => {
      const lane = lanes.find(l => l.type === def.id) || { type: def.id, items: [] };
      const items = lane.items || [];
      const milestoneCount = countMilestonesInLane(def.id);
      return [
        '<div class="yuyi-lt-lane" data-lane="' + esc(def.id) + '">',
        '  <div class="yuyi-lt-lane-header" style="background: linear-gradient(135deg, ' + def.color + ', #ffffff);">',
        '    <span class="yuyi-lt-lane-title" style="color: ' + def.text + ';">' + esc(def.label) + '</span>',
        '    <span class="yuyi-lt-lane-count" style="color: ' + def.text + ';">' + items.length + '</span>',
        '    ' + (milestoneCount > 0 ? '<span class="yuyi-lt-lane-ms" title="该泳道含里程碑">⭐ ' + milestoneCount + '</span>' : ''),
        '  </div>',
        '  <div class="yuyi-lt-lane-items" data-lane-items="' + esc(def.id) + '">',
        items.length === 0
          ? '<div class="yuyi-lt-lane-empty">无节点</div>'
          : items.map(it => renderLaneItem(def, it)).join(""),
        '  </div>',
        '</div>'
      ].join("");
    }).join("");
    // 绑定点击
    root.querySelectorAll(".yuyi-lt-item").forEach(el => {
      el.addEventListener("click", function () {
        const id = this.getAttribute("data-id") || "";
        const type = this.getAttribute("data-type") || "";
        onItemClick(id, type, this);
      });
    });
    // 渲染因果连线
    renderCausality();
  }

  function countMilestonesInLane(laneType) {
    if (!currentMilestones || !currentMilestones.length) return 0;
    let n = 0;
    for (const m of currentMilestones) {
      if (!m) continue;
      const ch = m.chain || [];
      if (ch.indexOf(laneType) >= 0) n++;
    }
    return n;
  }

  function renderLaneItem(def, it) {
    const ts = it.timestamp_ts || 0;
    return [
      '<div class="yuyi-lt-item" data-id="' + esc(it.id) + '" data-type="' + esc(it.type) + '" data-ts="' + esc(String(ts)) + '">',
      '  <div class="yuyi-lt-item-dot" style="background: ' + def.color + '; border-color: ' + def.text + ';"></div>',
      '  <div class="yuyi-lt-item-body">',
      '    <div class="yuyi-lt-item-title">' + esc(it.title || it.topic || it.id) + '</div>',
      '    <div class="yuyi-lt-item-time">' + esc(fmtTs(it.timestamp)) + '</div>',
      (it.summary ? '    <div class="yuyi-lt-item-sum">' + esc(it.summary.slice(0, 80)) + '</div>' : ''),
      '  </div>',
      '</div>'
    ].join("");
  }

  // ---- 因果连线 ----
  function renderCausality() {
    const svg = $("yuyi-lt-svg");
    if (!svg) return;
    const d = currentData || {};
    const edges = d.edges || [];
    // 取得泳道布局
    const lanesRoot = $("yuyi-lt-lanes");
    if (!lanesRoot) return;
    const lanes = lanesRoot.querySelectorAll(".yuyi-lt-lane");
    // 计算泳道 x 位置
    const laneX: Record<string, number> = {};
    lanes.forEach((el) => {
      const id = el.getAttribute("data-lane") || "";
      const rect = el.getBoundingClientRect();
      const wrap = $("yuyi-lt-lanes-wrap");
      const wr = wrap ? wrap.getBoundingClientRect() : null;
      if (wr) laneX[id] = rect.left - wr.left + rect.width / 2;
    });
    // 节点布局(从渲染出的 item 中获取)
    const wrap = $("yuyi-lt-lanes-wrap");
    if (!wrap) return;
    const wrapRect = wrap.getBoundingClientRect();
    svg.setAttribute("width", String(wrapRect.width));
    svg.setAttribute("height", String(wrapRect.height));
    svg.innerHTML = "";
    // 构建 item 位置索引
    const itemPos: Record<string, { x: number, y: number, type: string }> = {};
    lanesRoot.querySelectorAll(".yuyi-lt-item").forEach((el) => {
      const id = el.getAttribute("data-id") || "";
      const type = el.getAttribute("data-type") || "";
      const r = el.getBoundingClientRect();
      itemPos[id] = { x: r.left - wrapRect.left + r.width / 2, y: r.top - wrapRect.top + r.height / 2, type: type };
    });
    // 画线
    edges.forEach((e) => {
      const s = e.from;
      const t = e.to;
      const sp = itemPos[s];
      const tp = itemPos[t];
      if (!sp || !tp) return;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const dx = (tp.x - sp.x) * 0.5;
      const d_str = "M " + sp.x + " " + sp.y + " C " + (sp.x + dx) + " " + sp.y + ", " + (tp.x - dx) + " " + tp.y + ", " + tp.x + " " + tp.y;
      path.setAttribute("d", d_str);
      path.setAttribute("class", "yuyi-lt-edge");
      path.setAttribute("data-from", s);
      path.setAttribute("data-to", t);
      path.setAttribute("stroke", "rgba(150,150,200,0.45)");
      path.setAttribute("stroke-width", "1.4");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke-dasharray", "4 3");
      // 高亮悬停节点
      if (hoverNodeId && (s === hoverNodeId || t === hoverNodeId)) {
        path.setAttribute("stroke", "#6c63ff");
        path.setAttribute("stroke-width", "2.2");
        path.setAttribute("stroke-dasharray", "");
      }
      svg.appendChild(path);
    });
  }

  // ---- 节点点击 → Detail Panel ----
  function onItemClick(id, type, el) {
    selectedNodeId = id;
    const d = currentData || {};
    const lanes = d.lanes || [];
    let found = null;
    for (const ln of lanes) {
      const items = ln.items || [];
      for (const it of items) {
        if (it.id === id) { found = { item: it, lane: ln }; break; }
      }
      if (found) break;
    }
    const detail = $("yuyi-lt-detail");
    if (!detail) return;
    if (!found) {
      detail.style.display = "none";
      return;
    }
    const it = found.item;
    const def = laneDef(type);
    const evidenceHtml = (it.source_event_ids || []).map(eid => {
      return '<code class="yuyi-lt-ev-code">' + esc(eid) + '</code>';
    }).join(" ");
    detail.innerHTML = [
      '<div class="yuyi-lt-detail-inner" style="border-left: 4px solid ' + def.color + ';">',
      '  <div class="yuyi-lt-detail-head">',
      '    <span class="yuyi-lt-detail-type" style="background: ' + def.color + '; color: ' + def.text + ';">' + esc(def.label) + '</span>',
      '    <button type="button" class="yuyi-lt-detail-close" id="yuyi-lt-detail-close">×</button>',
      '  </div>',
      '  <h4 class="yuyi-lt-detail-title">' + esc(it.title || it.id) + '</h4>',
      '  <div class="yuyi-lt-detail-meta">',
      '    <span>⏱ ' + esc(it.timestamp || "--") + '</span>',
      '    <span>ID: <code>' + esc(it.id) + '</code></span>',
      '  </div>',
      (it.summary ? '  <p class="yuyi-lt-detail-sum">' + esc(it.summary) + '</p>' : ''),
      '  <div class="yuyi-lt-detail-ev">',
      '    <strong>Evidence:</strong> ' + (evidenceHtml || '<span class="yuyi-empty">无</span>'),
      '  </div>',
      '  <div class="yuyi-lt-detail-actions">',
      '    <button type="button" class="yuyi-lt-btn yuyi-lt-btn-why" data-id="' + esc(id) + '">为什么? → LifeGraph Why</button>',
      '  </div>',
      '</div>'
    ].join("");
    detail.style.display = "block";
    const closeBtn = $("yuyi-lt-detail-close");
    if (closeBtn) closeBtn.addEventListener("click", () => { detail.style.display = "none"; });
    const whyBtn = detail.querySelector(".yuyi-lt-btn-why");
    if (whyBtn) whyBtn.addEventListener("click", () => onWhyClick(id));
  }

  // ---- Why 跳转 ----
  async function onWhyClick(nodeId) {
    if (!nodeId || !global.YuyiDashboardAPI) return;
    try {
      const r = await global.YuyiDashboardAPI.lifeGraphWhy(nodeId);
      const d = r.data || {};
      const detail = $("yuyi-lt-detail");
      if (!detail) return;
      let html = "";
      if (d.fallback) {
        html = '<p class="yuyi-empty">无法解释: ' + esc(d.fallback_reason || "unknown") + '</p>';
      } else {
        const chain = d.chain || [];
        const conf = (typeof d.confidence === "number") ? d.confidence.toFixed(2) : "--";
        html = [
          '<div class="yuyi-lt-why-block">',
          '  <h5>Why? 影响链</h5>',
          '  <p class="yuyi-lt-why-chain">' + chain.map(t => '<span class="yuyi-lt-why-chip">' + esc(t) + '</span>').join(" <span class='yuyi-lt-why-arrow'>↓</span> ") + '</p>',
          '  <p class="yuyi-lt-why-meta">置信度: ' + esc(conf) + ' · 可追溯: ' + (d.traceable ? "是" : "否") + '</p>',
          '</div>'
        ].join("");
      }
      // 追加到 detail
      const old = detail.querySelector(".yuyi-lt-why-result");
      if (old) old.remove();
      const wrap = document.createElement("div");
      wrap.className = "yuyi-lt-why-result";
      wrap.innerHTML = html;
      detail.appendChild(wrap);
    } catch (e) {
      console.warn("life_timeline.onWhyClick 异常:", e);
    }
  }

  // ---- Milestones 标记 ----
  function renderMilestones() {
    const ms = $("yuyi-lt-milestones");
    if (!ms) return;
    const list = currentMilestones || [];
    if (!list.length) {
      ms.innerHTML = "";
      return;
    }
    ms.innerHTML = [
      '<h4 class="yuyi-lt-ms-title">⭐ Growth Points · 成长节点</h4>',
      '<div class="yuyi-lt-ms-list">',
      list.map(m => {
        const ch = (m.chain || []).map(c => '<span class="yuyi-lt-why-chip">' + esc(c) + '</span>').join(" <span class='yuyi-lt-why-arrow'>↓</span> ");
        const evIds = (m.evidence || []).map(e => '<code>' + esc(e) + '</code>').join(" ");
        return [
          '<div class="yuyi-lt-ms-item" data-id="' + esc((m.evidence && m.evidence[0]) || m.id) + '">',
          '  <div class="yuyi-lt-ms-head">',
          '    <span class="yuyi-lt-ms-type">' + esc(m.type) + '</span>',
          '    <span class="yuyi-lt-ms-time">' + esc(fmtTs(m.timestamp)) + '</span>',
          '  </div>',
          '  <div class="yuyi-lt-ms-title-text">' + esc(m.title || "") + '</div>',
          '  <p class="yuyi-lt-ms-chain">' + ch + '</p>',
          '  <p class="yuyi-lt-ms-ev">证据: ' + (evIds || '<span class="yuyi-empty">无</span>') + '</p>',
          (m.summary ? '  <p class="yuyi-lt-ms-sum">' + esc(m.summary) + '</p>' : ''),
          '  <button type="button" class="yuyi-lt-btn yuyi-lt-btn-why" data-id="' + esc((m.evidence && m.evidence[0]) || "") + '">跳转 Why →</button>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>'
    ].join("");
    ms.querySelectorAll(".yuyi-lt-btn-why").forEach(btn => {
      btn.addEventListener("click", function () {
        const id = this.getAttribute("data-id") || "";
        onWhyClick(id);
      });
    });
  }

  // ---- Hover 联动 ----
  function bindHover() {
    document.querySelectorAll(".yuyi-lt-item").forEach(el => {
      el.addEventListener("mouseenter", function () {
        hoverNodeId = this.getAttribute("data-id") || null;
        renderCausality();
      });
      el.addEventListener("mouseleave", function () {
        hoverNodeId = null;
        renderCausality();
      });
    });
  }

  // ---- 暴露 ----
  global.YuyiLifeTimeline = {
    init: initLifeTimeline,
    reload: load,
  };

  // 兼容旧版自动 init
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLifeTimeline);
  } else {
    setTimeout(initLifeTimeline, 0);
  }
})(window);
