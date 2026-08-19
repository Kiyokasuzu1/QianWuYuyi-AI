/* ========================================================
   Yuyi Memory Graph — 记忆关系网络可视化
   ========================================================
   使用 Canvas 力导向布局渲染记忆节点和关系连线。
   Phase 2.5: Understanding Layer 核心组件。
   ======================================================== */

const YuyiMemoryGraph = (function () {
  "use strict";

  const API_ENDPOINT = "/admin/api/cognitive/memory-graph";
  let canvas = null;
  let ctx = null;
  let nodes = [];
  let links = [];
  let animationId = null;
  let hoveredNode = null;
  let selectedNode = null;

  /* ============ 物理模拟参数 ============ */

  const SIMULATION = {
    repulsion: 800,      // 节点间斥力
    attraction: 0.01,    // 连线引力
    damping: 0.85,       // 速度衰减
    centerGravity: 0.005,// 向中心引力
    minDistance: 60,     // 最小距离
  };

  /* ============ 颜色映射 ============ */

  const TYPE_COLORS = {
    user: "#a5b4fc",
    event: "#86efac",
    memory: "#f9a8d4",
    growth: "#fde68a",
    unknown: "#94a3b8",
  };

  const TYPE_LABELS = {
    user: "用户",
    event: "事件",
    memory: "记忆",
    growth: "成长",
    unknown: "未知",
  };

  /* ============ 初始化 ============ */

  function init(canvasId) {
    canvas = document.getElementById(canvasId);
    if (!canvas) {
      console.error("[MemoryGraph] Canvas not found:", canvasId);
      return false;
    }

    ctx = canvas.getContext("2d");

    // 响应式尺寸
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);

    // 鼠标交互
    canvas.addEventListener("mousemove", handleMouseMove);
    canvas.addEventListener("click", handleClick);
    canvas.addEventListener("mouseleave", () => {
      hoveredNode = null;
      canvas.style.cursor = "default";
    });

    return true;
  }

  function resizeCanvas() {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
  }

  /* ============ 数据加载 ============ */

  async function load(mock = false) {
    try {
      const url = mock ? `${API_ENDPOINT}?mock=true` : API_ENDPOINT;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      nodes = data.nodes || [];
      links = data.links || [];

      // 初始化节点位置（圆形布局）
      const centerX = canvas.getBoundingClientRect().width / 2;
      const centerY = canvas.getBoundingClientRect().height / 2;
      const radius = Math.min(centerX, centerY) * 0.6;

      nodes.forEach((node, i) => {
        const angle = (i / nodes.length) * 2 * Math.PI;
        node.x = centerX + radius * Math.cos(angle);
        node.y = centerY + radius * Math.sin(angle);
        node.vx = 0;
        node.vy = 0;
      });

      // 启动动画
      if (!animationId) {
        animate();
      }

      return data;
    } catch (e) {
      console.error("[MemoryGraph] Load failed:", e);
      return null;
    }
  }

  /* ============ 力导向模拟 ============ */

  function simulate() {
    if (nodes.length === 0) return;

    const rect = canvas.getBoundingClientRect();
    const centerX = rect.width / 2;
    const centerY = rect.height / 2;

    // 斥力（节点间）
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const minDist = SIMULATION.minDistance + nodes[i].size / 2 + nodes[j].size / 2;

        if (dist < minDist * 2) {
          const force = SIMULATION.repulsion / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;

          nodes[i].vx -= fx;
          nodes[i].vy -= fy;
          nodes[j].vx += fx;
          nodes[j].vy += fy;
        }
      }
    }

    // 引力（连线）
    for (const link of links) {
      const source = nodes.find(n => n.id === link.source);
      const target = nodes.find(n => n.id === link.target);
      if (!source || !target) continue;

      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;

      const force = dist * SIMULATION.attraction * link.strength;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;

      source.vx += fx;
      source.vy += fy;
      target.vx -= fx;
      target.vy -= fy;
    }

    // 向中心引力
    for (const node of nodes) {
      const dx = centerX - node.x;
      const dy = centerY - node.y;
      node.vx += dx * SIMULATION.centerGravity;
      node.vy += dy * SIMULATION.centerGravity;
    }

    // 应用速度
    for (const node of nodes) {
      node.vx *= SIMULATION.damping;
      node.vy *= SIMULATION.damping;
      node.x += node.vx;
      node.y += node.vy;

      // 边界约束
      const margin = node.size;
      node.x = Math.max(margin, Math.min(rect.width - margin, node.x));
      node.y = Math.max(margin, Math.min(rect.height - margin, node.y));
    }
  }

  /* ============ 渲染 ============ */

  function render() {
    if (!ctx || !canvas) return;

    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);

    // 绘制连线
    for (const link of links) {
      const source = nodes.find(n => n.id === link.source);
      const target = nodes.find(n => n.id === link.target);
      if (!source || !target) continue;

      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.lineTo(target.x, target.y);
      ctx.strokeStyle = `rgba(165, 180, 252, ${0.2 + link.strength * 0.4})`;
      ctx.lineWidth = 1 + link.strength * 2;
      ctx.stroke();
    }

    // 绘制节点
    for (const node of nodes) {
      const color = TYPE_COLORS[node.type] || TYPE_COLORS.unknown;
      const isHovered = hoveredNode && hoveredNode.id === node.id;
      const isSelected = selectedNode && selectedNode.id === node.id;
      const size = node.size || 25;

      // 光晕
      if (isHovered || isSelected) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, size + 8, 0, Math.PI * 2);
        ctx.fillStyle = `${color}33`;
        ctx.fill();
      }

      // 节点圆
      ctx.beginPath();
      ctx.arc(node.x, node.y, size / 2, 0, Math.PI * 2);

      // 渐变填充
      const gradient = ctx.createRadialGradient(
        node.x - size / 6, node.y - size / 6, 0,
        node.x, node.y, size / 2
      );
      gradient.addColorStop(0, `${color}ee`);
      gradient.addColorStop(1, `${color}aa`);
      ctx.fillStyle = gradient;
      ctx.fill();

      // 边框
      ctx.strokeStyle = isHovered || isSelected ? "#ffffff" : `${color}`;
      ctx.lineWidth = isHovered || isSelected ? 2 : 1;
      ctx.stroke();

      // 标签
      ctx.fillStyle = "#4c3f7a";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const label = node.title.length > 8 ? node.title.substring(0, 8) + "..." : node.title;
      ctx.fillText(label, node.x, node.y + size / 2 + 12);
    }
  }

  function animate() {
    simulate();
    render();
    animationId = requestAnimationFrame(animate);
  }

  /* ============ 鼠标交互 ============ */

  function handleMouseMove(e) {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    hoveredNode = null;
    for (const node of nodes) {
      const dx = x - node.x;
      const dy = y - node.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < (node.size || 25) / 2 + 5) {
        hoveredNode = node;
        break;
      }
    }

    canvas.style.cursor = hoveredNode ? "pointer" : "default";
  }

  function handleClick(e) {
    if (hoveredNode) {
      selectedNode = hoveredNode;
      // 触发详情显示
      if (typeof showMemoryDetail === "function") {
        showMemoryDetail(hoveredNode);
      }
    }
  }

  /* ============ 销毁 ============ */

  function destroy() {
    if (animationId) {
      cancelAnimationFrame(animationId);
      animationId = null;
    }
    window.removeEventListener("resize", resizeCanvas);
  }

  /* ============ 公开 API ============ */

  return {
    init,
    load,
    destroy,
    getNodes: () => nodes,
    getLinks: () => links,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiMemoryGraph = YuyiMemoryGraph;
}