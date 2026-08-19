/* ========================================================
   Yuyi Radar — 认知雷达图
   ========================================================
   绘制五维能力雷达：创造力、好奇心、稳定度、记忆力、社交倾向。
   Phase 2.5: Understanding Layer 核心组件。
   ======================================================== */

const YuyiRadar = (function () {
  "use strict";

  const API_ENDPOINT = "/admin/api/cognitive/radar";
  let canvas = null;
  let ctx = null;
  let animationId = null;
  let currentData = null;
  let targetData = null;
  let progress = 0;

  /* ============ 五维配置 ============ */

  const DIMENSIONS = [
    { key: "creativity", label: "创造力", color: "#a5b4fc" },
    { key: "curiosity", label: "好奇心", color: "#86efac" },
    { key: "stability", label: "稳定度", color: "#fde68a" },
    { key: "memory", label: "记忆力", color: "#f9a8d4" },
    { key: "social", label: "社交", color: "#67e8f9" },
  ];

  /* ============ 初始化 ============ */

  function init(canvasId) {
    canvas = document.getElementById(canvasId);
    if (!canvas) {
      console.error("[Radar] Canvas not found:", canvasId);
      return false;
    }

    ctx = canvas.getContext("2d");

    // 响应式尺寸
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);

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

  async function load(mock = true) {
    try {
      const url = mock ? `${API_ENDPOINT}?mock=true` : API_ENDPOINT;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // 初始化动画
      if (!currentData) {
        currentData = {
          creativity: 0,
          curiosity: 0,
          stability: 0,
          memory: 0,
          social: 0,
        };
      }
      targetData = {
        creativity: data.creativity || 70,
        curiosity: data.curiosity || 70,
        stability: data.stability || 70,
        memory: data.memory || 70,
        social: data.social || 70,
      };

      progress = 0;
      if (!animationId) {
        animate();
      }

      return data;
    } catch (e) {
      console.error("[Radar] Load failed:", e);
      return null;
    }
  }

  /* ============ 动画与渲染 ============ */

  function animate() {
    // 缓动动画
    if (targetData && progress < 1) {
      progress += 0.02;
      progress = Math.min(1, progress);

      for (const key in targetData) {
        const start = currentData[key] || 0;
        const end = targetData[key];
        currentData[key] = start + (end - start) * easeOutCubic(progress);
      }
    }

    render();

    if (progress >= 1) {
      // 动画完成，继续扫描动画
      scanAnimation();
    }

    animationId = requestAnimationFrame(animate);
  }

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  let scanAngle = 0;
  function scanAnimation() {
    scanAngle += 0.01;
  }

  function render() {
    if (!ctx || !canvas) return;

    const rect = canvas.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    const cx = w / 2;
    const cy = h / 2;
    const maxRadius = Math.min(w, h) / 2 - 40;

    ctx.clearRect(0, 0, w, h);

    // 绘制背景网格（五边形）
    for (let level = 1; level <= 5; level++) {
      const r = (maxRadius / 5) * level;
      drawPentagon(cx, cy, r, "rgba(165, 180, 252, 0.15)");
    }

    // 绘制轴线
    const angles = getAngles();
    for (let i = 0; i < 5; i++) {
      const angle = angles[i];
      const x = cx + maxRadius * Math.cos(angle);
      const y = cy + maxRadius * Math.sin(angle);

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(x, y);
      ctx.strokeStyle = "rgba(165, 180, 252, 0.3)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // 绘制雷达区域
    if (currentData) {
      const points = [];
      for (let i = 0; i < 5; i++) {
        const dim = DIMENSIONS[i];
        const value = currentData[dim.key] || 0;
        const r = (value / 100) * maxRadius;
        const angle = angles[i];
        points.push({
          x: cx + r * Math.cos(angle),
          y: cy + r * Math.sin(angle),
        });
      }

      // 填充区域
      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i++) {
        ctx.lineTo(points[i].x, points[i].y);
      }
      ctx.closePath();

      const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, maxRadius);
      gradient.addColorStop(0, "rgba(165, 180, 252, 0.4)");
      gradient.addColorStop(1, "rgba(165, 180, 252, 0.1)");
      ctx.fillStyle = gradient;
      ctx.fill();

      // 边框
      ctx.strokeStyle = "rgba(165, 180, 252, 0.8)";
      ctx.lineWidth = 2;
      ctx.stroke();

      // 顶点
      for (let i = 0; i < points.length; i++) {
        const p = points[i];
        ctx.beginPath();
        ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = DIMENSIONS[i].color;
        ctx.fill();
      }
    }

    // 绘制扫描线（动画效果）
    if (progress >= 1) {
      const scanR = maxRadius;
      const scanX = cx + scanR * Math.cos(scanAngle);
      const scanY = cy + scanR * Math.sin(scanAngle);

      const scanGradient = ctx.createLinearGradient(cx, cy, scanX, scanY);
      scanGradient.addColorStop(0, "rgba(165, 180, 252, 0)");
      scanGradient.addColorStop(0.5, "rgba(165, 180, 252, 0.3)");
      scanGradient.addColorStop(1, "rgba(165, 180, 252, 0)");

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(scanX, scanY);
      ctx.strokeStyle = scanGradient;
      ctx.lineWidth = 20;
      ctx.stroke();
    }

    // 绘制标签
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (let i = 0; i < 5; i++) {
      const dim = DIMENSIONS[i];
      const angle = angles[i];
      const labelR = maxRadius + 25;
      const x = cx + labelR * Math.cos(angle);
      const y = cy + labelR * Math.sin(angle);

      ctx.fillStyle = dim.color;
      ctx.font = "600 13px sans-serif";
      ctx.fillText(dim.label, x, y - 10);

      // 数值
      const value = currentData ? currentData[dim.key] || 0 : 0;
      ctx.fillStyle = "#4c3f7a";
      ctx.font = "500 12px sans-serif";
      ctx.fillText(Math.round(value), x, y + 6);
    }
  }

  function drawPentagon(cx, cy, r, color) {
    const angles = getAngles();
    ctx.beginPath();
    for (let i = 0; i < 5; i++) {
      const x = cx + r * Math.cos(angles[i]);
      const y = cy + r * Math.sin(angles[i]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function getAngles() {
    // 从顶部开始，顺时针
    const startAngle = -Math.PI / 2;
    const angles = [];
    for (let i = 0; i < 5; i++) {
      angles.push(startAngle + (i * 2 * Math.PI) / 5);
    }
    return angles;
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
    getData: () => currentData,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiRadar = YuyiRadar;
}