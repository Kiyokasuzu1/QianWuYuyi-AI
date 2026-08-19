/* =============================================================
 * life_graph.js —— Life Graph 页面渲染 (Step 8.4.2 升级)
 *
 * 数据源:
 *  - /api/dashboard/v2/life-graph/overview
 *  - /api/dashboard/v2/life-graph/timeline
 *  - /api/dashboard/v2/life-graph/path?from_id=...&to_id=...
 *  - /api/dashboard/v2/life-graph/node/<id>          (Step 8.4.2)
 *  - /api/dashboard/v2/life-graph/node/<id>/neighbors (Step 8.4.2)
 *  - /api/dashboard/v2/life-graph/why/<id>            (Step 8.4.2)
 *
 * Step 8.4.2 新增能力:
 *  - D3 风格 force-directed layout(自实现,无 CDN 依赖)
 *  - 节点拖动 / 缩放 / 平移
 *  - 节点点击 → 右侧 Detail Panel
 *  - "为什么?" 按钮 → 调用 /why/<id> → 展示因果链 + 路径动画
 *  - 路径动画:上游红色 / 下游蓝色 / 边流动
 *  - 类型过滤(全开/单类型切换)
 *  - 节点类型:memory / reflection / interest / action / goal / belief / trait_change
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // ---- 颜色 / 标签 ----
  const TYPE_COLOR = {
    memory: "#a6c8ff",
    reflection: "#c9b6ff",
    interest: "#ffb6d5",
    action: "#ffd6a8",
    goal: "#b8e6c1",
    belief: "#ffe6a8",
    trait_change: "#d4baff",
  };
  const TYPE_TEXT_COLOR = {
    memory: "#3a4a78",
    reflection: "#523b86",
    interest: "#8a3a64",
    action: "#7a4a1f",
    goal: "#2f6b3c",
    belief: "#7a6010",
    trait_change: "#5d3892",
  };
  const TYPE_LABEL = {
    memory: "记忆",
    reflection: "反思",
    interest: "兴趣",
    action: "行动",
    goal: "目标",
    belief: "信念",
    trait_change: "特质变化",
  };
  const RELATION_LABEL = {
    memory_to_reflection: "促成反思",
    reflection_to_interest: "激发兴趣",
    interest_to_goal: "推动目标",
    goal_to_action: "产生行动",
    reflection_to_belief: "塑造信念",
    belief_to_trait_change: "驱动特质变化",
    topic_link: "主题关联",
  };
  // 关系颜色
  const RELATION_COLOR = {
    memory_to_reflection: "#a6c8ff",
    reflection_to_interest: "#c9b6ff",
    interest_to_goal: "#ffb6d5",
    goal_to_action: "#b8e6c1",
    reflection_to_belief: "#ffe6a8",
    belief_to_trait_change: "#d4baff",
    topic_link: "#cfd8ef",
  };

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

  // ---- State ----
  let currentTypeFilter = "";
  let currentGraph = { nodes: [], edges: [], counts: {} };
  let currentNodeIndex = {};
  let selectedNodeId = null;
  let layoutNodes = []; // 模拟位置: {id, x, y, vx, vy, type, raw}
  let layoutEdges = [];
  let highlightUpstream = new Set();
  let highlightDownstream = new Set();
  let whyPath = []; // 当前 Why 动画路径
  let svgTransform = { x: 0, y: 0, k: 1 };
  let dragNode = null;
  let isPanning = false;
  let panStart = null;
  let forceTimer = null;

  // ============================================================
  // 加载 overview
  // ============================================================
  async function loadOverview() {
    if (!API || !API.lifeGraphOverview) return;
    try {
      const types = currentTypeFilter ? [currentTypeFilter] : null;
      const r = await API.lifeGraphOverview(200, 400, types);
      const d = r.data || {};
      currentGraph = {
        nodes: d.nodes || [],
        edges: d.edges || [],
        counts: d.counts || {},
        node_count: d.node_count || 0,
        edge_count: d.edge_count || 0,
      };
      currentNodeIndex = {};
      for (const n of currentGraph.nodes) currentNodeIndex[n.id] = n;
      const c = currentGraph.counts;
      setText("ylg-stat-memory", c.memory || 0);
      setText("ylg-stat-reflection", c.reflection || 0);
      setText("ylg-stat-interest", c.interest || 0);
      setText("ylg-stat-action", c.action || 0);
      setText("ylg-stat-goal", c.goal || 0);
      setText("ylg-stat-belief", c.belief || 0);
      setText("ylg-stat-trait", c.trait_change || 0);
      setText("ylg-count-tag", `nodes ${currentGraph.node_count} · edges ${currentGraph.edge_count}`);
      initForceLayout(currentGraph);
      renderGraph();
      if (r.fallback) toast("Life Graph 不可用,已 fallback");
    } catch (e) {
      console.warn("loadOverview 失败:", e);
    }
  }

  // ============================================================
  // 简易 force-directed layout(自实现,无外部依赖)
  // ============================================================
  function initForceLayout(g) {
    const W = 1200, H = 520;
    const cx = W / 2, cy = H / 2;
    const nodes = g.nodes || [];
    const edges = g.edges || [];
    // 计算度数
    const degree = {};
    for (const e of edges) {
      degree[e.source] = (degree[e.source] || 0) + 1;
      degree[e.target] = (degree[e.target] || 0) + 1;
    }
    layoutNodes = nodes.map((n, i) => {
      const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
      const r = 180 + (degree[n.id] || 0) * 12;
      return {
        id: n.id,
        type: n.type,
        label: n.label || n.topic || n.id,
        raw: n,
        x: cx + Math.cos(angle) * r,
        y: cy + Math.sin(angle) * r,
        vx: 0, vy: 0,
      };
    });
    layoutEdges = edges
      .filter(e => currentNodeIndex[e.source] && currentNodeIndex[e.target])
      .map(e => ({ source: e.source, target: e.target, relation: e.relation || "" }));
  }

  // 跑 N 次力迭代
  function tickForce(iterations) {
    if (!layoutNodes.length) return;
    const cx = 600, cy = 260;
    const repulsion = 4200;     // 库仑斥力
    const linkDist = 110;        // 理想边长
    const linkK = 0.04;          // 胡克系数
    const centerK = 0.012;       // 向心力
    const damping = 0.78;
    const maxV = 12;
    const pos = {};
    for (const n of layoutNodes) pos[n.id] = n;
    for (let it = 0; it < iterations; it++) {
      // 斥力
      for (let i = 0; i < layoutNodes.length; i++) {
        const a = layoutNodes[i];
        for (let j = i + 1; j < layoutNodes.length; j++) {
          const b = layoutNodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { d2 = 1; dx = 0.01; dy = 0.01; }
          const f = repulsion / d2;
          const len = Math.sqrt(d2) || 1;
          const fx = (dx / len) * f;
          const fy = (dy / len) * f;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
      }
      // 弹簧
      for (const e of layoutEdges) {
        const a = pos[e.source], b = pos[e.target];
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        const diff = (len - linkDist) * linkK;
        const fx = (dx / len) * diff;
        const fy = (dy / len) * diff;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
      // 向心
      for (const n of layoutNodes) {
        n.vx += (cx - n.x) * centerK;
        n.vy += (cy - n.y) * centerK;
      }
      // 应用速度
      for (const n of layoutNodes) {
        n.vx *= damping; n.vy *= damping;
        if (n.vx > maxV) n.vx = maxV; else if (n.vx < -maxV) n.vx = -maxV;
        if (n.vy > maxV) n.vy = maxV; else if (n.vy < -maxV) n.vy = -maxV;
        n.x += n.vx; n.y += n.vy;
        // 边界
        if (n.x < 30) n.x = 30;
        if (n.x > 1170) n.x = 1170;
        if (n.y < 30) n.y = 30;
        if (n.y > 490) n.y = 490;
      }
    }
  }

  function findNode(id) {
    for (const n of layoutNodes) if (n.id === id) return n;
    return null;
  }

  // ============================================================
  // 渲染
  // ============================================================
  function renderGraph() {
    const svg = $("ylg-svg");
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!layoutNodes.length) {
      const t = document.createElementNS(SVG_NS, "text");
      t.setAttribute("x", 600); t.setAttribute("y", 260);
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("fill", "#7a7a9a");
      t.setAttribute("font-size", "16");
      t.textContent = "暂无 Life Graph 数据";
      svg.appendChild(t);
      return;
    }
    // 跑力迭代
    tickForce(120);
    // defs
    const defs = document.createElementNS(SVG_NS, "defs");
    defs.innerHTML = `
      <radialGradient id="selfGradient" cx="50%" cy="50%" r="60%">
        <stop offset="0%" stop-color="#ffe5f1"/>
        <stop offset="100%" stop-color="#c9b6ff"/>
      </radialGradient>
      <filter id="ylgShadow" x="-50%" y="-50%" width="200%" height="200%">
        <feGaussianBlur stdDeviation="1.4" />
        <feOffset dx="0" dy="1" result="off"/>
        <feComponentTransfer><feFuncA type="linear" slope="0.35"/></feComponentTransfer>
        <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
      <marker id="arrow-default" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 8 4 L 0 8 z" fill="#9aa6c2"/>
      </marker>
      <marker id="arrow-up" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 8 4 L 0 8 z" fill="#ff5a87"/>
      </marker>
      <marker id="arrow-down" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
        <path d="M 0 0 L 8 4 L 0 8 z" fill="#5a9bff"/>
      </marker>
    `;
    svg.appendChild(defs);

    // 主 group(支持 transform)
    const rootG = document.createElementNS(SVG_NS, "g");
    rootG.setAttribute("id", "ylg-root");
    rootG.setAttribute("transform", `translate(${svgTransform.x}, ${svgTransform.y}) scale(${svgTransform.k})`);
    svg.appendChild(rootG);

    // 边
    const edgesG = document.createElementNS(SVG_NS, "g");
    edgesG.setAttribute("id", "ylg-edges");
    edgesG.setAttribute("fill", "none");
    rootG.appendChild(edgesG);
    for (const e of layoutEdges) {
      const a = findNode(e.source), b = findNode(e.target);
      if (!a || !b) continue;
      const path = document.createElementNS(SVG_NS, "path");
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      // 弧度
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.sqrt(dx * dx + dy * dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const curve = Math.min(50, len * 0.18);
      const c1x = a.x + nx * curve, c1y = a.y + ny * curve;
      const c2x = b.x + nx * curve, c2y = b.y + ny * curve;
      path.setAttribute("d", `M ${a.x} ${a.y} Q ${c1x} ${c1y}, ${(a.x + b.x) / 2} ${(a.y + b.y) / 2} T ${b.x} ${b.y}`);
      path.setAttribute("stroke", RELATION_COLOR[e.relation] || "#cfd8ef");
      path.setAttribute("stroke-width", "1.4");
      path.setAttribute("stroke-opacity", "0.55");
      path.setAttribute("data-source", e.source);
      path.setAttribute("data-target", e.target);
      path.setAttribute("marker-end", "url(#arrow-default)");
      edgesG.appendChild(path);
    }

    // 节点
    const nodesG = document.createElementNS(SVG_NS, "g");
    nodesG.setAttribute("id", "ylg-nodes");
    rootG.appendChild(nodesG);
    for (const n of layoutNodes) {
      const g = createNodeEl(n);
      nodesG.appendChild(g);
    }

    // 启动慢速力(让节点轻微"呼吸"位置)
    startForceLoop();

    // 绑定交互
    bindSvgInteractions(svg);
  }

  function createNodeEl(n) {
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "ylg-node");
    g.setAttribute("transform", `translate(${n.x}, ${n.y})`);
    g.setAttribute("data-id", n.id);
    g.setAttribute("data-type", n.type || "");
    g.style.cursor = "grab";
    const r = 18;
    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("r", r);
    circle.setAttribute("fill", TYPE_COLOR[n.type] || "#cfd8ef");
    circle.setAttribute("stroke", "rgba(255,255,255,0.9)");
    circle.setAttribute("stroke-width", "1.5");
    circle.setAttribute("filter", "url(#ylgShadow)");
    g.appendChild(circle);
    // type 标识小点
    const tag = document.createElementNS(SVG_NS, "text");
    tag.setAttribute("text-anchor", "middle");
    tag.setAttribute("dy", "0.35em");
    tag.setAttribute("fill", TYPE_TEXT_COLOR[n.type] || "#3a4a78");
    tag.setAttribute("font-size", "10");
    tag.setAttribute("font-weight", "600");
    const lab = String(n.label || n.id || "");
    tag.textContent = lab.length > 6 ? lab.slice(0, 5) + "…" : lab;
    g.appendChild(tag);

    // type 小徽章
    const badge = document.createElementNS(SVG_NS, "rect");
    badge.setAttribute("x", -r);
    badge.setAttribute("y", r + 2);
    badge.setAttribute("width", 28);
    badge.setAttribute("height", 12);
    badge.setAttribute("rx", 4);
    badge.setAttribute("fill", TYPE_COLOR[n.type] || "#cfd8ef");
    badge.setAttribute("opacity", "0.8");
    g.appendChild(badge);
    const bt = document.createElementNS(SVG_NS, "text");
    bt.setAttribute("text-anchor", "middle");
    bt.setAttribute("y", r + 11);
    bt.setAttribute("fill", TYPE_TEXT_COLOR[n.type] || "#3a4a78");
    bt.setAttribute("font-size", "8");
    bt.textContent = (TYPE_LABEL[n.type] || n.type || "").slice(0, 4);
    g.appendChild(bt);

    // hover
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${TYPE_LABEL[n.type] || n.type}: ${n.label}${n.raw && n.raw.topic ? "\n主题: " + n.raw.topic : ""}`;
    g.appendChild(title);

    // click → detail
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectNode(n.id);
    });
    // drag
    g.addEventListener("mousedown", (ev) => {
      ev.stopPropagation();
      dragNode = n;
      g.style.cursor = "grabbing";
    });
    return g;
  }

  // ============================================================
  // 慢速力循环
  // ============================================================
  function startForceLoop() {
    if (forceTimer) clearInterval(forceTimer);
    forceTimer = setInterval(() => {
      if (!layoutNodes.length) return;
      // 只在用户未交互时跑
      if (dragNode || isPanning) return;
      tickForce(2);
      updatePositions();
    }, 1800);
  }

  function updatePositions() {
    const root = $("ylg-root");
    if (!root) return;
    const edgesG = $("ylg-edges");
    if (edgesG) {
      // 重新画边(轻量)
      while (edgesG.firstChild) edgesG.removeChild(edgesG.firstChild);
      for (const e of layoutEdges) {
        const a = findNode(e.source), b = findNode(e.target);
        if (!a || !b) continue;
        const path = document.createElementNS(SVG_NS, "path");
        const dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        const nx = -dy / len, ny = dx / len;
        const curve = Math.min(50, len * 0.18);
        const c1x = a.x + nx * curve, c1y = a.y + ny * curve;
        const c2x = b.x + nx * curve, c2y = b.y + ny * curve;
        path.setAttribute("d", `M ${a.x} ${a.y} Q ${c1x} ${c1y}, ${(a.x + b.x) / 2} ${(a.y + b.y) / 2} T ${b.x} ${b.y}`);
        path.setAttribute("stroke", RELATION_COLOR[e.relation] || "#cfd8ef");
        path.setAttribute("stroke-width", "1.4");
        path.setAttribute("stroke-opacity", "0.55");
        path.setAttribute("data-source", e.source);
        path.setAttribute("data-target", e.target);
        const up = highlightUpstream.has(e.source) && highlightUpstream.has(e.target);
        const down = highlightDownstream.has(e.source) && highlightDownstream.has(e.target);
        if (up) {
          path.setAttribute("stroke", "#ff5a87");
          path.setAttribute("stroke-width", "3");
          path.setAttribute("marker-end", "url(#arrow-up)");
          path.setAttribute("stroke-opacity", "0.95");
        } else if (down) {
          path.setAttribute("stroke", "#5a9bff");
          path.setAttribute("stroke-width", "3");
          path.setAttribute("marker-end", "url(#arrow-down)");
          path.setAttribute("stroke-opacity", "0.95");
        } else {
          path.setAttribute("marker-end", "url(#arrow-default)");
        }
        // 路径动画 dash(why path)
        if (whyPath.length > 1) {
          for (let i = 0; i < whyPath.length - 1; i++) {
            const aId = whyPath[i], bId = whyPath[i + 1];
            if ((e.source === aId && e.target === bId) || (e.source === bId && e.target === aId)) {
              path.setAttribute("stroke", "#8a3a64");
              path.setAttribute("stroke-width", "3.4");
              path.setAttribute("stroke-opacity", "1");
              path.setAttribute("stroke-dasharray", "6 4");
              path.setAttribute("marker-end", "url(#arrow-up)");
              const anim = document.createElementNS(SVG_NS, "animate");
              anim.setAttribute("attributeName", "stroke-dashoffset");
              anim.setAttribute("from", "0");
              anim.setAttribute("to", "20");
              anim.setAttribute("dur", "1.2s");
              anim.setAttribute("repeatCount", "indefinite");
              path.appendChild(anim);
            }
          }
        }
        edgesG.appendChild(path);
      }
    }
    const nodesG = $("ylg-nodes");
    if (nodesG) {
      const childMap = {};
      for (const c of nodesG.children) {
        childMap[c.getAttribute("data-id")] = c;
      }
      for (const n of layoutNodes) {
        const g = childMap[n.id];
        if (!g) continue;
        g.setAttribute("transform", `translate(${n.x}, ${n.y})`);
        // 选中/上/下游高亮
        const circle = g.querySelector("circle");
        if (n.id === selectedNodeId) {
          circle.setAttribute("stroke", "#ff5a87");
          circle.setAttribute("stroke-width", "3");
        } else if (highlightUpstream.has(n.id) || highlightDownstream.has(n.id)) {
          circle.setAttribute("stroke", highlightUpstream.has(n.id) ? "#ff5a87" : "#5a9bff");
          circle.setAttribute("stroke-width", "2.4");
        } else {
          circle.setAttribute("stroke", "rgba(255,255,255,0.9)");
          circle.setAttribute("stroke-width", "1.5");
        }
      }
    }
  }

  // ============================================================
  // 交互:拖动 / 缩放 / 平移
  // ============================================================
  function bindSvgInteractions(svg) {
    // 鼠标位置 → svg 坐标
    function clientToSvg(clientX, clientY) {
      const rect = svg.getBoundingClientRect();
      const x = (clientX - rect.left) * (1200 / rect.width);
      const y = (clientY - rect.top) * (520 / rect.height);
      return { x: (x - svgTransform.x) / svgTransform.k, y: (y - svgTransform.y) / svgTransform.k };
    }
    svg.addEventListener("mousemove", (ev) => {
      if (dragNode) {
        const p = clientToSvg(ev.clientX, ev.clientY);
        dragNode.x = p.x; dragNode.y = p.y;
        dragNode.vx = 0; dragNode.vy = 0;
        const g = ev.target.closest(".ylg-node");
        if (g) g.setAttribute("transform", `translate(${p.x}, ${p.y})`);
        updatePositions();
      } else if (isPanning) {
        const dx = ev.clientX - panStart.x;
        const dy = ev.clientY - panStart.y;
        svgTransform.x = panStart.tx + dx;
        svgTransform.y = panStart.ty + dy;
        const root = $("ylg-root");
        if (root) root.setAttribute("transform", `translate(${svgTransform.x}, ${svgTransform.y}) scale(${svgTransform.k})`);
      }
    });
    svg.addEventListener("mouseup", () => {
      if (dragNode) {
        const g = document.querySelector(`.ylg-node[data-id="${dragNode.id}"]`);
        if (g) g.style.cursor = "grab";
        dragNode = null;
      }
      isPanning = false;
    });
    svg.addEventListener("mouseleave", () => {
      dragNode = null;
      isPanning = false;
    });
    // pan:右键或中键,这里用空白处拖动
    svg.addEventListener("mousedown", (ev) => {
      if (ev.target.closest(".ylg-node")) return;
      isPanning = true;
      panStart = { x: ev.clientX, y: ev.clientY, tx: svgTransform.x, ty: svgTransform.y };
    });
    // wheel zoom
    svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const delta = -ev.deltaY * 0.0015;
      const newK = Math.max(0.4, Math.min(2.5, svgTransform.k * (1 + delta)));
      svgTransform.k = newK;
      const root = $("ylg-root");
      if (root) root.setAttribute("transform", `translate(${svgTransform.x}, ${svgTransform.y}) scale(${svgTransform.k})`);
    }, { passive: false });
    // 空白处点击取消选中
    svg.addEventListener("click", (ev) => {
      if (ev.target.closest(".ylg-node")) return;
      selectedNodeId = null;
      highlightUpstream.clear();
      highlightDownstream.clear();
      whyPath = [];
      updatePositions();
      clearDetailPanel();
    });
  }

  // ============================================================
  // 节点选中 + 详情
  // ============================================================
  function selectNode(id) {
    if (!id) return;
    selectedNodeId = id;
    const node = currentNodeIndex[id];
    if (!node) {
      // 节点在 index 中可能缺失(极端情况)
      renderDetailFallback(id);
      return;
    }
    renderDetail(node);
    // 计算上下游
    highlightUpstream = new Set();
    highlightDownstream = new Set();
    for (const e of (currentGraph.edges || [])) {
      if (e.source === id) highlightDownstream.add(e.target);
      if (e.target === id) highlightUpstream.add(e.source);
    }
    updatePositions();
  }

  function renderDetailFallback(id) {
    setText("ylg-detail-tag", "未知");
    setHtml("ylg-detail", `<p class="yuyi-empty">未找到节点 ${esc(id)} 的本地数据</p>`);
  }

  function clearDetailPanel() {
    setText("ylg-detail-tag", "未选择");
    setHtml("ylg-detail", '<p class="yuyi-empty">从图上选择一个节点,这里会显示它的来源、证据链与时间戳。</p>');
  }

  function renderDetail(node) {
    if (!node) return;
    const id = node.id || "";
    const type = node.type || "";
    setText("ylg-detail-tag", TYPE_LABEL[type] || type);
    const ev = (node.source_event_ids || []).join(", ") || "--";
    const t = node.timestamp || node.timestamp_iso || "--";
    const lines = [];
    lines.push(`<p><strong>ID:</strong> <code>${esc(id)}</code></p>`);
    lines.push(`<p><strong>类型:</strong> ${esc(TYPE_LABEL[type] || type)}</p>`);
    if (node.topic) lines.push(`<p><strong>主题:</strong> ${esc(node.topic)}</p>`);
    if (node.label) lines.push(`<p><strong>标签:</strong> ${esc(node.label)}</p>`);
    lines.push(`<p><strong>时间:</strong> ${esc(fmtTime(t))}</p>`);
    lines.push(`<p><strong>来源事件:</strong> <code>${esc(ev)}</code></p>`);
    if (node.trend) lines.push(`<p><strong>趋势:</strong> ${esc(node.trend)}</p>`);
    if (node.action_type) lines.push(`<p><strong>行动类型:</strong> ${esc(node.action_type)}</p>`);
    if (node.status) lines.push(`<p><strong>状态:</strong> ${esc(node.status)}</p>`);
    if (node.confidence !== undefined && node.confidence !== null)
      lines.push(`<p><strong>置信度:</strong> ${esc((Number(node.confidence) || 0).toFixed(2))}</p>`);
    if (node.priority !== undefined && node.priority !== null)
      lines.push(`<p><strong>优先级:</strong> ${esc((Number(node.priority) || 0).toFixed(2))}</p>`);
    if (node.trait_name) lines.push(`<p><strong>特质:</strong> ${esc(node.trait_name)}</p>`);
    if (node.delta !== undefined && node.delta !== null)
      lines.push(`<p><strong>Δ:</strong> ${esc(node.delta)}</p>`);
    if (node.evidence) lines.push(`<p><strong>证据:</strong> ${esc(node.evidence)}</p>`);

    // 入边 / 出边
    const incoming = [];
    const outgoing = [];
    for (const e of (currentGraph.edges || [])) {
      if (e.target === id) incoming.push(e);
      if (e.source === id) outgoing.push(e);
    }
    if (incoming.length) {
      lines.push(`<p><strong>↑ 入边(${incoming.length},上游):</strong></p><ul>`);
      for (const e of incoming) {
        lines.push(`<li>${esc(RELATION_LABEL[e.relation] || e.relation)} ← <code>${esc(e.source)}</code> · ${esc(e.evidence || "")}</li>`);
      }
      lines.push(`</ul>`);
    }
    if (outgoing.length) {
      lines.push(`<p><strong>↓ 出边(${outgoing.length},下游):</strong></p><ul>`);
      for (const e of outgoing) {
        lines.push(`<li>${esc(RELATION_LABEL[e.relation] || e.relation)} → <code>${esc(e.target)}</code> · ${esc(e.evidence || "")}</li>`);
      }
      lines.push(`</ul>`);
    }

    // 操作按钮:Why
    lines.push(`<div style="display:flex; gap:6px; flex-wrap: wrap; margin-top: 10px;">
      <button type="button" class="yuyi-btn yuyi-btn--primary" id="ylg-btn-why">为什么?</button>
      <button type="button" class="yuyi-btn yuyi-btn--ghost" id="ylg-btn-neighbors">查看邻居(深度 2)</button>
      <button type="button" class="yuyi-btn yuyi-btn--ghost" id="ylg-btn-detail">远端详情</button>
    </div>`);
    // Why 结果容器
    lines.push(`<div id="ylg-why-result" style="margin-top: 10px;"></div>`);
    lines.push(`<div id="ylg-neighbors-result" style="margin-top: 10px;"></div>`);
    lines.push(`<div id="ylg-detail-extra" style="margin-top: 10px;"></div>`);
    setHtml("ylg-detail", lines.join(""));

    // 绑定按钮
    const whyBtn = $("ylg-btn-why");
    if (whyBtn) whyBtn.addEventListener("click", () => askWhy(id));
    const nbBtn = $("ylg-btn-neighbors");
    if (nbBtn) nbBtn.addEventListener("click", () => loadNeighbors(id, 2));
    const detBtn = $("ylg-btn-detail");
    if (detBtn) detBtn.addEventListener("click", () => loadNodeDetail(id));

    // 自动填入 path-from
    const fromIn = $("ylg-path-from");
    if (fromIn && !fromIn.value) fromIn.value = id;
  }

  // ============================================================
  // Why 解释
  // ============================================================
  async function askWhy(nodeId) {
    if (!API || !API.lifeGraphWhy) return;
    const out = $("ylg-why-result");
    if (out) out.innerHTML = '<p class="yuyi-empty">解释中…</p>';
    try {
      const r = await API.lifeGraphWhy(nodeId);
      const d = r.data || {};
      if (d.fallback) {
        const reason = d.fallback_reason || "unknown";
        if (out) out.innerHTML = `<p class="yuyi-empty">无法解释(${esc(reason)})</p>`;
        whyPath = [];
        updatePositions();
        return;
      }
      const chain = d.chain || [];
      const nodePath = d.node_path || [];
      const rootCauses = d.root_causes || [];
      const edges = d.edges_used || [];
      const conf = (typeof d.confidence === "number") ? d.confidence.toFixed(2) : "--";
      // 渲染链路
      const chainHtml = chain.map((t) => {
        const label = TYPE_LABEL[t] || t;
        const color = TYPE_COLOR[t] || "#cfd8ef";
        const text = TYPE_TEXT_COLOR[t] || "#3a4a78";
        return `<span style="display:inline-block; padding: 2px 8px; border-radius: 10px; background: ${color}; color: ${text}; font-size: 11px; margin: 1px;">${esc(label)}</span>`;
      }).join(" <span style='color:#7a7a9a'>→</span> ");
      // 渲染 root causes
      const rcHtml = rootCauses.map((rc) => {
        return `<li>
          <span style="display:inline-block; padding: 1px 6px; border-radius: 8px; background: ${TYPE_COLOR[rc.type] || "#cfd8ef"}; color: ${TYPE_TEXT_COLOR[rc.type] || "#3a4a78"}; font-size: 10px; margin-right: 4px;">${esc(TYPE_LABEL[rc.type] || rc.type)}</span>
          <code style="font-size: 10px;">${esc(rc.id)}</code>
          <span style="color:#7a7a9a; font-size: 11px;">· strength ${esc((Number(rc.strength) || 0).toFixed(2))}</span>
          <div style="font-size: 12px; margin-top: 2px; color:#3a3a52;">${esc(rc.summary || "")}</div>
        </li>`;
      }).join("");
      if (out) out.innerHTML = `
        <div style="background: linear-gradient(135deg, rgba(255,245,250,0.55), rgba(225,235,255,0.55)); border-radius: 10px; padding: 10px;">
          <p style="margin: 0 0 6px 0;"><strong>为什么?</strong></p>
          <p style="margin: 0 0 6px 0;">${chainHtml}</p>
          <p style="margin: 0; font-size: 11px; color: #6a6a8a;">置信度 ${esc(conf)} · 链路长度 ${nodePath.length} · 可追溯 ${d.traceable ? "是" : "否"}</p>
          ${rcHtml ? `<p style="margin: 8px 0 4px 0;"><strong>根因:</strong></p><ul style="margin: 0; padding-left: 18px;">${rcHtml}</ul>` : ""}
        </div>
      `;
      // 启动路径动画
      whyPath = nodePath.slice();
      // 高亮上下行(粗略标记整条链路)
      highlightUpstream = new Set(nodePath.slice(0, -1));
      highlightDownstream = new Set(nodePath.slice(-1));
      updatePositions();
    } catch (e) {
      if (out) out.innerHTML = `<p class="yuyi-empty">查询失败: ${esc(e && e.message ? e.message : e)}</p>`;
    }
  }

  async function loadNeighbors(nodeId, depth) {
    if (!API || !API.lifeGraphNodeNeighbors) return;
    const out = $("ylg-neighbors-result");
    if (out) out.innerHTML = '<p class="yuyi-empty">加载邻居…</p>';
    try {
      const r = await API.lifeGraphNodeNeighbors(nodeId, depth || 1);
      const d = r.data || {};
      if (d.fallback) {
        if (out) out.innerHTML = `<p class="yuyi-empty">邻居不可用(${esc(d.fallback_reason || "unknown")})</p>`;
        return;
      }
      const nodes = d.nodes || [];
      const edges = d.edges || [];
      const centerId = d.center || nodeId;
      const items = nodes.map((n) => {
        const isCenter = n.is_center;
        return `<li>
          <span style="display:inline-block; padding: 1px 6px; border-radius: 8px; background: ${TYPE_COLOR[n.type] || "#cfd8ef"}; color: ${TYPE_TEXT_COLOR[n.type] || "#3a4a78"}; font-size: 10px; margin-right: 4px;">${esc(TYPE_LABEL[n.type] || n.type)}</span>
          <code style="font-size: 10px;">${esc(n.id)}</code>
          ${isCenter ? '<span style="color:#ff5a87; font-size: 10px; margin-left: 4px;">[中心]</span>' : ''}
          <span style="color:#7a7a9a; font-size: 11px;">${esc((n.label || n.topic || "").slice(0, 32))}</span>
        </li>`;
      }).join("");
      if (out) out.innerHTML = `
        <div style="background: rgba(255,255,255,0.5); border-radius: 10px; padding: 8px 10px;">
          <p style="margin: 0 0 6px 0;"><strong>邻居(d=${esc(depth || 1)})</strong> · 节点 ${nodes.length} · 边 ${edges.length}</p>
          <ul style="margin: 0; padding-left: 18px; max-height: 180px; overflow:auto;">${items}</ul>
        </div>
      `;
    } catch (e) {
      if (out) out.innerHTML = `<p class="yuyi-empty">邻居加载失败: ${esc(e && e.message ? e.message : e)}</p>`;
    }
  }

  async function loadNodeDetail(nodeId) {
    if (!API || !API.lifeGraphNode) return;
    const out = $("ylg-detail-extra");
    if (out) out.innerHTML = '<p class="yuyi-empty">远端详情加载中…</p>';
    try {
      const r = await API.lifeGraphNode(nodeId);
      const d = r.data || {};
      if (d.fallback) {
        if (out) out.innerHTML = `<p class="yuyi-empty">节点不可用(${esc(d.fallback_reason || "unknown")})</p>`;
        return;
      }
      const node = d.node || {};
      const evidence = d.evidence || [];
      const neighbors = d.neighbors || [];
      const evHtml = evidence.map((e) => {
        return `<li>
          <span style="display:inline-block; padding: 1px 6px; border-radius: 8px; background: rgba(180,200,255,0.5); color:#3a4a78; font-size: 10px; margin-right: 4px;">${esc(e.source_type || "")}</span>
          <code style="font-size: 10px;">${esc(e.source_id || "")}</code>
          <span style="color:#7a7a9a; font-size: 11px;">· ${esc(e.reason || "")}</span>
        </li>`;
      }).join("");
      const nbHtml = neighbors.slice(0, 10).map((n) => {
        return `<li>
          <span style="display:inline-block; padding: 1px 6px; border-radius: 8px; background: ${TYPE_COLOR[n.neighbor_type] || "#cfd8ef"}; color: ${TYPE_TEXT_COLOR[n.neighbor_type] || "#3a4a78"}; font-size: 10px; margin-right: 4px;">${esc(TYPE_LABEL[n.neighbor_type] || n.neighbor_type)}</span>
          <code style="font-size: 10px;">${esc(n.neighbor_id)}</code>
          <span style="color:#7a7a9a; font-size: 11px;">${esc(n.direction === "out" ? "↓" : "↑")} ${esc(RELATION_LABEL[n.relation] || n.relation)}</span>
        </li>`;
      }).join("");
      if (out) out.innerHTML = `
        <div style="background: rgba(255,255,255,0.5); border-radius: 10px; padding: 8px 10px;">
          <p style="margin: 0 0 6px 0;"><strong>远端节点详情</strong> · confidence ${esc(((r && r.confidence) || 0).toFixed ? (r.confidence || 0).toFixed(2) : "--")} · fallback ${r.fallback ? "是" : "否"}</p>
          <p style="margin: 0 0 4px 0; font-size: 11px; color:#6a6a8a;">Title: ${esc(node.title || node.label || node.id || "")}</p>
          ${evHtml ? `<p style="margin: 6px 0 4px 0;"><strong>Evidence</strong></p><ul style="margin: 0; padding-left: 18px; max-height: 140px; overflow:auto;">${evHtml}</ul>` : '<p style="margin: 4px 0; color:#7a7a9a;">无 evidence</p>'}
          ${nbHtml ? `<p style="margin: 6px 0 4px 0;"><strong>邻居预览</strong></p><ul style="margin: 0; padding-left: 18px; max-height: 140px; overflow:auto;">${nbHtml}</ul>` : ''}
        </div>
      `;
    } catch (e) {
      if (out) out.innerHTML = `<p class="yuyi-empty">远端详情失败: ${esc(e && e.message ? e.message : e)}</p>`;
    }
  }

  // ============================================================
  // 时间线
  // ============================================================
  async function loadTimeline() {
    if (!API || !API.lifeGraphTimeline) return;
    setHtml("ylg-timeline", '<li class="yuyi-events__item yuyi-events__item--empty">加载中…</li>');
    try {
      const r = await API.lifeGraphTimeline(100);
      const d = r.data || {};
      const items = d.items || [];
      if (!items.length) {
        setHtml("ylg-timeline", '<li class="yuyi-events__item yuyi-events__item--empty">暂无时间线</li>');
        return;
      }
      setHtml(
        "ylg-timeline",
        items.map((it) => {
          return `<li class="ylg-timeline__item">
            <div class="ylg-timeline__head">
              <span class="ylg-timeline__type" style="background: ${TYPE_COLOR[it.type]}; color: ${TYPE_TEXT_COLOR[it.type]};">${esc(TYPE_LABEL[it.type] || it.type)}</span>
              <span class="ylg-timeline__label">${esc(it.label || "")}</span>
            </div>
            <small class="ylg-timeline__meta">${esc(fmtTime(it.timestamp))} · ${esc(it.summary || "")}</small>
            ${it.evidence ? `<p class="ylg-timeline__evidence">${esc(it.evidence)}</p>` : ""}
          </li>`;
        }).join("")
      );
    } catch (e) {
      setHtml("ylg-timeline", '<li class="yuyi-events__item yuyi-events__item--empty">加载失败</li>');
    }
  }

  // ============================================================
  // 路径查询
  // ============================================================
  async function runPath() {
    if (!API || !API.lifeGraphPath) return;
    const fromIn = $("ylg-path-from");
    const toIn = $("ylg-path-to");
    const fromId = fromIn ? (fromIn.value || "").trim() : "";
    const toId = toIn ? (toIn.value || "").trim() : "";
    if (!fromId || !toId) {
      toast("请输入 from_id 和 to_id");
      return;
    }
    setHtml("ylg-path-list", '<li class="yuyi-list__empty">查询中…</li>');
    try {
      const r = await API.lifeGraphPath(fromId, toId, 8);
      const d = r.data || {};
      if (d.found === false) {
        setHtml("ylg-path-list", `<li class="yuyi-list__empty">未找到路径(${esc(d.fallback_reason || "no_path")})</li>`);
        return;
      }
      const path = d.path || [];
      const edges = d.edges_used || [];
      const items = [];
      for (let i = 0; i < path.length; i++) {
        const p = path[i];
        items.push(`<li class="ylg-path__node">
          <span class="ylg-path__type" style="background: ${TYPE_COLOR[p.type]}; color: ${TYPE_TEXT_COLOR[p.type]};">${esc(TYPE_LABEL[p.type] || p.type)}</span>
          <strong>${esc(p.label || p.id)}</strong>
          <small>${esc(p.topic || "")} · ${esc(fmtTime(p.timestamp))}</small>
          <code>${esc(p.id)}</code>
        </li>`);
        if (i < edges.length) {
          const e = edges[i];
          items.push(`<li class="ylg-path__edge">
            <span class="ylg-path__arrow">↓</span>
            <span>${esc(RELATION_LABEL[e.relation] || e.relation)}</span>
            <small>${esc(e.evidence || "")}</small>
          </li>`);
        }
      }
      setHtml("ylg-path-list", items.join(""));
    } catch (e) {
      setHtml("ylg-path-list", '<li class="yuyi-list__empty">查询失败</li>');
    }
  }

  // ============================================================
  // 生命周期
  // ============================================================
  async function refresh() {
    await Promise.all([loadOverview(), loadTimeline()]);
  }

  function init() {
    const root = document.querySelector('[data-page-id="life-graph"]');
    if (!root) return;
    refresh();
    const btn = $("ylg-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
    const tSel = $("ylg-type-filter");
    if (tSel) tSel.addEventListener("change", () => {
      currentTypeFilter = tSel.value || "";
      loadOverview();
    });
    const pathBtn = $("ylg-path-go");
    if (pathBtn) pathBtn.addEventListener("click", runPath);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
