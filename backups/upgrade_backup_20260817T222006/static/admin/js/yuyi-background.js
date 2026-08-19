/* ========================================================
   Yuyi Background — Canvas 粒子引擎 (增强版)
   ========================================================
   轻量粒子系统：星尘/光点/波纹/柔云/数据流
   根据情绪状态变化粒子颜色/密度/速度。
   支持鼠标交互、粒子连接、拖尾效果。
   不依赖任何框架。
   ======================================================== */

const YuyiBackground = (function () {
  "use strict";

  const MOOD_CONFIG = {
    calm: {
      colors: ["#a5b4fc", "#c4b5fd", "#e0e7ff"],
      count: 25, speed: 0.3, size: [1, 3],
      particle: "dot", trail: false, connect: true,
    },
    happy: {
      colors: ["#f9a8d4", "#fbcfe8", "#fce7f3", "#f0abfc"],
      count: 40, speed: 0.5, size: [1, 2.5],
      particle: "sparkle", trail: true, connect: true,
    },
    excited: {
      colors: ["#f472b6", "#f9a8d4", "#f0abfc", "#a78bfa"],
      count: 60, speed: 0.8, size: [1, 3],
      particle: "sparkle", trail: true, connect: true,
    },
    sad: {
      colors: ["#94a3b8", "#cbd5e1", "#e2e8f0"],
      count: 15, speed: 0.15, size: [1, 2],
      particle: "dot", trail: false, connect: false,
    },
    angry: {
      colors: ["#f87171", "#fca5a5", "#fecaca"],
      count: 35, speed: 0.6, size: [1, 2.5],
      particle: "dot", trail: false, connect: false,
    },
    surprised: {
      colors: ["#a78bfa", "#f0abfc", "#67e8f9"],
      count: 45, speed: 0.7, size: [1, 3],
      particle: "sparkle", trail: true, connect: true,
    },
    fearful: {
      colors: ["#94a3b8", "#a5b4fc", "#c4b5fd"],
      count: 20, speed: 0.2, size: [1, 2],
      particle: "dot", trail: false, connect: false,
    },
    worried: {
      colors: ["#fbbf24", "#fcd34d", "#fef3c7"],
      count: 30, speed: 0.35, size: [1, 2.5],
      particle: "dot", trail: false, connect: true,
    },
    burst: {
      colors: ["#c4b5fd", "#f9a8d4", "#67e8f9", "#fbbf24"],
      count: 50, speed: 0.8, size: [1, 3],
      particle: "sparkle", trail: true, connect: true,
    },
    flicker: {
      colors: ["#f87171", "#fbbf24", "#fca5a5"],
      count: 40, speed: 0.7, size: [1, 2.5],
      particle: "dot", trail: false, connect: false,
    },
    slow: {
      colors: ["#94a3b8", "#c4b5fd", "#e2e8f0"],
      count: 12, speed: 0.1, size: [1, 2],
      particle: "dot", trail: false, connect: false,
    },
    normal: {
      colors: ["#a5b4fc", "#c4b5fd", "#f9a8d4"],
      count: 25, speed: 0.3, size: [1, 3],
      particle: "dot", trail: false, connect: true,
    },
  };

  let canvas = null;
  let ctx = null;
  let particles = [];
  let animId = null;
  let currentMood = "calm";
  let width = 0;
  let height = 0;
  let dpr = 1;
  let running = false;
  let mouseX = -1000;
  let mouseY = -1000;
  let mouseActive = false;
  let mouseTimer = null;

  /* ============ 粒子类 ============ */

  class Particle {
    constructor(config) {
      this.reset(config, true);
    }

    reset(config, randomY = false) {
      this.x = Math.random() * width;
      this.y = randomY ? Math.random() * height : height + 10;
      this.size = config.size[0] + Math.random() * (config.size[1] - config.size[0]);
      this.color = config.colors[Math.floor(Math.random() * config.colors.length)];
      this.speed = config.speed * (0.5 + Math.random() * 0.8);
      this.drift = (Math.random() - 0.5) * 0.3;
      this.phase = Math.random() * Math.PI * 2;
      this.phaseSpeed = 0.01 + Math.random() * 0.02;
      this.alpha = 0.3 + Math.random() * 0.5;
      this.particleType = config.particle;
      this.life = 0;
      this.maxLife = 200 + Math.random() * 300;
      /* 拖尾历史 */
      this.trail = config.trail ? [] : null;
      this.trailLength = 8 + Math.floor(Math.random() * 8);
    }

    update(config) {
      this.life++;
      this.y -= this.speed;
      this.x += Math.sin(this.phase) * this.drift;
      this.phase += this.phaseSpeed;

      /* 鼠标轻微吸引 */
      if (mouseActive) {
        const dx = mouseX - this.x;
        const dy = mouseY - this.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 150) {
          const force = (150 - dist) / 150 * 0.02;
          this.x += dx * force;
          this.y += dy * force;
        }
      }

      /* 记录拖尾 */
      if (this.trail) {
        this.trail.push({ x: this.x, y: this.y, alpha: this.alpha });
        if (this.trail.length > this.trailLength) {
          this.trail.shift();
        }
      }

      if (this.y < -10 || this.life > this.maxLife) {
        this.reset(config);
      }
    }

    draw(ctx) {
      /* 绘制拖尾 */
      if (this.trail && this.trail.length > 1) {
        this.drawTrail(ctx);
      }

      ctx.save();
      ctx.globalAlpha = this.alpha * (0.5 + 0.5 * Math.sin(this.phase));
      ctx.fillStyle = this.color;

      if (this.particleType === "sparkle") {
        this.drawSparkle(ctx);
      } else {
        this.drawDot(ctx);
      }
      ctx.restore();
    }

    drawDot(ctx) {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * dpr, 0, Math.PI * 2);
      ctx.fill();
      /* 光晕 */
      if (this.size > 1.5) {
        ctx.globalAlpha *= 0.3;
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size * dpr * 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    drawSparkle(ctx) {
      const s = this.size * dpr;
      ctx.translate(this.x, this.y);
      ctx.rotate(this.phase * 0.5);
      ctx.beginPath();
      for (let i = 0; i < 4; i++) {
        const angle = (i * Math.PI) / 2;
        ctx.moveTo(0, 0);
        ctx.lineTo(Math.cos(angle) * s * 2, Math.sin(angle) * s * 2);
      }
      ctx.lineWidth = s * 0.6;
      ctx.strokeStyle = this.color;
      ctx.stroke();
      /* 中心光点 */
      ctx.globalAlpha *= 0.8;
      ctx.beginPath();
      ctx.arc(0, 0, s * 0.4, 0, Math.PI * 2);
      ctx.fill();
    }

    drawTrail(ctx) {
      const t = this.trail;
      if (t.length < 2) return;
      ctx.save();
      for (let i = 0; i < t.length - 1; i++) {
        const alpha = (i / t.length) * 0.2 * t[i].alpha;
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = this.color;
        ctx.lineWidth = this.size * 0.5 * (i / t.length);
        ctx.beginPath();
        ctx.moveTo(t[i].x, t[i].y);
        ctx.lineTo(t[i + 1].x, t[i + 1].y);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  /* ============ 粒子连接效果 ============ */

  function drawConnections(config) {
    if (!config.connect) return;
    const connectDist = 120;
    const maxConnections = 3;

    for (let i = 0; i < particles.length; i++) {
      let connections = 0;
      for (let j = i + 1; j < particles.length; j++) {
        if (connections >= maxConnections) break;
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < connectDist) {
          const alpha = (1 - dist / connectDist) * 0.15;
          ctx.save();
          ctx.globalAlpha = alpha;
          ctx.strokeStyle = particles[i].color;
          ctx.lineWidth = 0.5;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
          ctx.restore();
          connections++;
        }
      }
    }
  }

  /* ============ 引擎 ============ */

  function init(canvasEl) {
    canvas = canvasEl || document.getElementById("particle-canvas");
    if (!canvas) return false;

    ctx = canvas.getContext("2d");
    resize();
    window.addEventListener("resize", resize);
    setupMouseInteraction();
    start();
    return true;
  }

  function setupMouseInteraction() {
    document.addEventListener("mousemove", (e) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      mouseActive = true;
      if (mouseTimer) clearTimeout(mouseTimer);
      mouseTimer = setTimeout(() => { mouseActive = false; }, 2000);
    });
  }

  function resize() {
    if (!canvas) return;
    dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    width = rect.width;
    height = rect.height;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);
  }

  function start() {
    if (running) return;
    running = true;
    const config = MOOD_CONFIG[currentMood] || MOOD_CONFIG.calm;
    particles = Array.from({ length: config.count }, () => new Particle(config));
    animate();
  }

  function stop() {
    running = false;
    if (animId) cancelAnimationFrame(animId);
  }

  function animate() {
    if (!running) return;

    const config = MOOD_CONFIG[currentMood] || MOOD_CONFIG.calm;

    ctx.clearRect(0, 0, width, height);

    if (particles.length < config.count) {
      while (particles.length < config.count) {
        particles.push(new Particle(config));
      }
    } else if (particles.length > config.count) {
      particles.length = config.count;
    }

    /* 绘制连接 */
    drawConnections(config);

    particles.forEach(p => {
      p.update(config);
      p.draw(ctx);
    });

    animId = requestAnimationFrame(animate);
  }

  function setMood(mood, particleType) {
    if (!MOOD_CONFIG[mood]) {
      mood = "calm";
    }
    currentMood = mood;

    if (particleType && MOOD_CONFIG[particleType]) {
      const pt = MOOD_CONFIG[particleType];
      particles.forEach(p => {
        p.particleType = pt.particle;
        p.trail = pt.trail ? [] : null;
        p.color = pt.colors[Math.floor(Math.random() * pt.colors.length)];
        p.size = pt.size[0] + Math.random() * (pt.size[1] - pt.size[0]);
        p.speed = pt.speed * (0.5 + Math.random() * 0.8);
      });
    } else {
      const config = MOOD_CONFIG[mood];
      particles.forEach(p => {
        p.particleType = config.particle;
        p.trail = config.trail ? [] : null;
        p.color = config.colors[Math.floor(Math.random() * config.colors.length)];
        p.size = config.size[0] + Math.random() * (config.size[1] - config.size[0]);
        p.speed = config.speed * (0.5 + Math.random() * 0.8);
      });
    }
  }

  function getMood() {
    return currentMood;
  }

  /* ============ 公开 API ============ */

  return {
    init,
    start,
    stop,
    setMood,
    getMood,
    resize,
    MOOD_CONFIG,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiBackground = YuyiBackground;
}
