/* ============================================
   羽依 Yuyi Console — Dashboard 渲染
   模块卡片(含依赖) · 角色状态联动(通过 YuyiCharacter) · 羽依核心状态
   ============================================ */

const MODULE_ICONS = {
  screen: "module/screen",
  control: "module/control",
  remote: "module/remote",
  memory: "memory/water-drop",
  personality: "core/heart",
  emotion: "emotion/heartbeat",
  initiative: "core/star",
  token_opt: "module/neural",
  vision: "module/screen",
  voice: "emotion/ripple",
  config: "module/config",
  audit: "module/audit",
};

const STATUS_LABELS = {
  running: { text: "运行中", class: "yuyi-status--calm" },
  idle: { text: "空闲", class: "yuyi-status--thinking" },
  stopped: { text: "已停止", class: "yuyi-status--worry" },
  error: { text: "异常", class: "yuyi-status--worry" },
  degraded: { text: "降级", class: "yuyi-status--notice" },
  starting: { text: "启动中", class: "yuyi-status--thinking" },
  unknown: { text: "未知", class: "yuyi-status--worry" },
};

/* ============ SVG 图标加载辅助 ============ */

function getIconPath(name, category) {
  if (category && name) return `/admin/icons/${category}/${name}.svg`;
  return "";
}

function moduleIconHTML(moduleName) {
  const iconRef = MODULE_ICONS[moduleName];
  if (!iconRef) return "";
  const [cat, name] = iconRef.split("/");
  const path = `/admin/icons/${cat}/${name}.svg`;
  return `<img src="${path}" alt="${moduleName}" class="module-icon" loading="lazy">`;
}

/* ============ 模块卡片渲染（含依赖关系 + 新图标） ============ */

function renderModules(modules) {
  const grid = document.getElementById("modules-grid");
  if (!grid) return;

  document.getElementById("module-count").textContent = `${modules.length} 个模块`;

  grid.innerHTML = "";
  const template = document.getElementById("module-card-template");

  modules.forEach(mod => {
    const card = template.content.firstElementChild.cloneNode(true);
    const statusClass = mod.hb_status || (mod.enabled ? "idle" : "stopped");

    card.classList.remove("running", "idle", "stopped", "error", "degraded", "starting", "unknown");
    card.classList.add(statusClass);

    // 注入 Yuyi Design System 卡片类
    card.classList.add("yuyi-card", "yuyi-card--hoverable");

    card.querySelector(".module-name").textContent = mod.display || mod.name;
    card.querySelector(".module-version").textContent = mod.version || "0.0.0";

    const status = STATUS_LABELS[statusClass] || STATUS_LABELS.unknown;
    card.querySelector(".status-text").textContent = status.text;

    const uptime = card.querySelector(".status-uptime");
    if (mod.hb_last_tick !== undefined && mod.hb_last_tick >= 0) {
      uptime.textContent = `${mod.hb_last_tick.toFixed(0)}秒前`;
    } else if (mod.hb_last_tick === -1) {
      uptime.textContent = "心跳超时";
    } else {
      uptime.textContent = "—";
    }

    card.querySelector(".reload-mode").textContent = mod.reload_mode || "hot";
    card.querySelector(".error-count").textContent = mod.hb_errors || 0;

    // 依赖关系展示
    const depsContainer = card.querySelector(".module-deps");
    if (depsContainer) {
      depsContainer.innerHTML = "";
      const deps = mod.dependencies || [];
      if (deps.length === 0) {
        depsContainer.innerHTML = '<span class="dep-none">无依赖</span>';
      } else {
        deps.forEach(dep => {
          const tag = document.createElement("span");
          tag.className = `dep-tag ${dep.satisfied ? "satisfied" : "unsatisfied"}`;
          tag.textContent = `${dep.satisfied ? "✓" : "✗"} ${dep.name}`;
          depsContainer.appendChild(tag);
        });
      }
    }

    // 图标注入
    const nameEl = card.querySelector(".module-name");
    if (nameEl) {
      const iconHTML = moduleIconHTML(mod.name);
      if (iconHTML) {
        nameEl.insertAdjacentHTML("beforebegin", iconHTML);
      }
    }

    const toggleBtn = card.querySelector(".module-toggle");
    const toggleText = toggleBtn.querySelector(".toggle-text");
    if (mod.enabled) {
      toggleBtn.classList.add("stopped");
      toggleText.textContent = "关闭";
    } else {
      toggleBtn.classList.remove("stopped");
      toggleText.textContent = "启动";
    }
    toggleBtn.onclick = (e) => {
      e.stopPropagation();
      toggleModule(mod.name);
    };

    const reloadBtn = card.querySelector(".module-reload");
    reloadBtn.onclick = (e) => {
      e.stopPropagation();
      reloadModule(mod.name);
    };

    const menuBtn = card.querySelector(".module-menu");
    if (menuBtn) {
      menuBtn.onclick = (e) => {
        e.stopPropagation();
        showModuleMenu(e, mod);
      };
    }

    grid.appendChild(card);
  });

  if (modules.length === 0) {
    grid.innerHTML = '<div class="loading">暂无模块</div>';
  }
}

function showModuleMenu(event, mod) {
  const x = event.clientX;
  const y = event.clientY;

  const existing = document.getElementById("context-menu");
  if (existing) existing.remove();

  const menu = document.createElement("div");
  menu.id = "context-menu";
  menu.style.cssText = `
    position: fixed; z-index: 9999;
    left: ${x}px; top: ${y}px;
    background: rgba(255,255,255,0.95);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(165,180,252,0.25);
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(165,180,252,0.2);
    padding: 8px;
    min-width: 160px;
    font-size: 13px;
  `;

  const items = [
    { label: mod.enabled ? "关闭模块" : "启动模块", action: () => toggleModule(mod.name) },
    { label: "重载模块", action: () => reloadModule(mod.name) },
    { label: "查看配置", action: () => switchView("config") },
  ];

  items.forEach(item => {
    const btn = document.createElement("div");
    btn.textContent = item.label;
    btn.style.cssText = `
      padding: 8px 12px; border-radius: 8px; cursor: pointer;
      transition: all 0.15s; color: #4c3f7a; font-weight: 500;
    `;
    btn.onmouseenter = () => btn.style.background = "rgba(165,180,252,0.2)";
    btn.onmouseleave = () => btn.style.background = "transparent";
    btn.onclick = () => { item.action(); menu.remove(); };
    menu.appendChild(btn);
  });

  document.body.appendChild(menu);

  setTimeout(() => {
    const closeMenu = (e) => {
      if (!menu.contains(e.target)) {
        menu.remove();
        document.removeEventListener("click", closeMenu);
      }
    };
    document.addEventListener("click", closeMenu);
  }, 10);
}

/* ============ 角色状态联动（通过 YuyiCharacter 控制器） ============ */

function renderCharacterState(character) {
  if (!character) return;

  if (typeof YuyiCharacter !== "undefined") {
    YuyiCharacter.setState({
      emotion: character.emotion || "平静",
      expression: character.expression || "smile",
      message: character.message || "正在为您待命~",
      energy: character.energy !== undefined ? character.energy : 50,
    });
    return;
  }

  // 降级方案：直接操作 SVG
  renderCharacterStateFallback(character);
}

function renderCharacterStateFallback(character) {
  const EXPRESSION_FACES = {
    smile:    { eyes: "M48 60 Q50 56 52 60", mouth: "M55 74 Q60 78 65 74" },
    happy:    { eyes: "M48 58 Q50 54 52 58", mouth: "M53 72 Q60 82 67 72" },
    sad:      { eyes: "M48 62 Q50 66 52 62", mouth: "M55 77 Q60 73 65 77" },
    excited:  { eyes: "M48 57 Q52 53 52 58", mouth: "M52 71 Q60 84 68 71" },
  };

  const expression = character.expression || "smile";
  const face = EXPRESSION_FACES[expression] || EXPRESSION_FACES.smile;

  const leftEye = document.getElementById("char-eye-left");
  const rightEye = document.getElementById("char-eye-right");
  const mouth = document.getElementById("char-mouth");

  if (leftEye) leftEye.setAttribute("d", face.eyes);
  if (rightEye) rightEye.setAttribute("d", face.eyes.replace(/48|52/g, m => m === "48" ? "68" : "72"));
  if (mouth) mouth.setAttribute("d", face.mouth);

  const msgEl = document.getElementById("character-message");
  if (msgEl && character.message) {
    msgEl.textContent = character.message;
  }

  const energyBar = document.getElementById("character-energy-bar");
  const energyLabel = document.getElementById("character-energy-label");
  if (energyBar) energyBar.style.width = `${character.energy || 50}%`;
  if (energyLabel) energyLabel.textContent = `能量 ${character.energy || 50}%`;
}

/* ============ 羽依核心生命状态渲染 ============ */

function renderYuyiCore(core) {
  if (!core) return;

  const container = document.getElementById("yuyi-core-display");
  if (!container) return;

  const thinking = core.thinking || {};
  const memory = core.memory || {};
  const stability = core.emotion_stability || 0;
  const growth = core.growth || {};

  const thinkingIcon = {
    normal: "🧠", degraded: "⚠️", offline: "💤",
  }[thinking.status] || "🧠";

  const stabilityColor = stability >= 80 ? "#34d399" : stability >= 50 ? "#fbbf24" : "#f87171";
  const memoryColor = memory.percent < 70 ? "#34d399" : memory.percent < 90 ? "#fbbf24" : "#f87171";

  container.innerHTML = `
    <div class="core-item">
      <div class="core-icon">${thinkingIcon}</div>
      <div class="core-info">
        <div class="core-label">思考模块</div>
        <div class="core-value">${thinking.detail || "正常"}</div>
      </div>
    </div>
    <div class="core-item">
      <div class="core-icon">💾</div>
      <div class="core-info">
        <div class="core-label">记忆容量</div>
        <div class="core-bar-wrap">
          <div class="core-bar" style="width:${memory.percent || 0}%;background:${memoryColor}"></div>
        </div>
        <div class="core-value">${memory.usage || 0} / ${memory.total || 0}</div>
      </div>
    </div>
    <div class="core-item">
      <div class="core-icon">💗</div>
      <div class="core-info">
        <div class="core-label">情绪稳定度</div>
        <div class="core-bar-wrap">
          <div class="core-bar" style="width:${stability}%;background:${stabilityColor}"></div>
        </div>
        <div class="core-value">${stability}%</div>
      </div>
    </div>
    <div class="core-item">
      <div class="core-icon">🌱</div>
      <div class="core-info">
        <div class="core-label">成长阶段</div>
        <div class="core-value">阶段 ${growth.stage || 1} · ${growth.label || "初始"}</div>
      </div>
    </div>
  `;
}

/* ============ 情绪渲染 ============ */

function renderEmotion(yuyi) {
  if (!yuyi) return;

  if (yuyi.greeting) {
    const greetingEl = document.getElementById("greeting");
    if (greetingEl) greetingEl.textContent = yuyi.greeting;
  }

  const emotion = yuyi.emotion || {};
  const emotionPrimary = document.getElementById("emotion-primary");
  if (emotionPrimary && emotion.primary) {
    emotionPrimary.textContent = `♡ ${emotion.primary}`;
  }

  const intensityBar = document.getElementById("emotion-intensity-bar");
  const intensityLabel = document.getElementById("emotion-intensity-label");
  if (intensityBar && intensityLabel) {
    const intensity = Math.round((emotion.intensity || 0.5) * 100);
    intensityBar.style.width = `${intensity}%`;
    intensityLabel.textContent = `强度 ${intensity}%`;
  }

  const emotionDisplay = document.getElementById("emotion-display");
  if (emotionDisplay && (emotion.valence !== undefined || emotion.arousal !== undefined)) {
    const valenceEl = emotionDisplay.querySelector("#pv-valence");
    const arousalEl = emotionDisplay.querySelector("#pv-arousal");
    if (valenceEl) valenceEl.textContent = `效价: ${(emotion.valence || 0).toFixed(2)}`;
    if (arousalEl) arousalEl.textContent = `唤醒: ${(emotion.arousal || 0).toFixed(2)}`;
  }
}

/* ============ 人格特质渲染 ============ */

function renderPersonalityTraits(yuyi) {
  const container = document.getElementById("traits-display");
  if (!container) return;

  const personality = yuyi.personality;
  if (!personality || typeof personality !== "object") {
    container.innerHTML = '<div style="font-size:13px;color:var(--c-text-muted);">暂无人格数据</div>';
    return;
  }

  const iconMap = {
    "开放性": "core/star",
    "严谨性": "module/neural",
    "外向性": "emotion/ripple",
    "宜人性": "core/heart",
    "神经质": "emotion/heartbeat",
  };

  container.innerHTML = "";
  Object.entries(personality).forEach(([name, data]) => {
    const value = typeof data === "object" ? data.value : data;
    const trend = typeof data === "object" ? data.trend : "";
    const iconRef = iconMap[name];
    const iconHTML = iconRef ? `<img src="/admin/icons/${iconRef}.svg" class="trait-icon" alt="" loading="lazy">` : "";

    const item = document.createElement("div");
    item.className = "trait-item";
    item.innerHTML = `
      <span class="trait-name">${iconHTML} ${name} ${trend ? `<span style="color:var(--c-text-muted);font-size:10px;">${trend}</span>` : ""}</span>
      <div class="trait-bar"><div class="trait-fill" style="width:${value}%"></div></div>
      <span class="trait-value">${value}</span>
    `;
    container.appendChild(item);
  });
}

/* ============ Dashboard 数据整合 ============ */

function handleDashboardData(data) {
  if (!data) return;

  renderEmotion(data.yuyi);
  renderPersonalityTraits(data.yuyi);

  if (data.yuyi && data.yuyi.character) {
    renderCharacterState(data.yuyi.character);
  }

  if (data.yuyi && data.yuyi.core) {
    renderYuyiCore(data.yuyi.core);
  }

  if (data.online_agents > 0) {
    const welcomeSub = document.querySelector(".welcome-sub");
    if (welcomeSub && !welcomeSub.querySelector(".agent-online")) {
      const badge = document.createElement("span");
      badge.className = "agent-online";
      badge.style.cssText = "margin-left:8px;font-size:11px;color:#34d399;font-weight:600;";
      badge.textContent = `● ${data.online_agents} 个代理在线`;
      welcomeSub.appendChild(badge);
    }
  }
}

/* ============ 实时时钟 ============ */

function updateClock() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, "0");
  const m = String(now.getMinutes()).padStart(2, "0");
  const s = String(now.getSeconds()).padStart(2, "0");
  const el = document.getElementById("current-time");
  if (el) el.textContent = `${h}:${m}:${s}`;
}

/* ============ 初始化 ============ */

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", () => {
    updateClock();
    setInterval(updateClock, 1000);
  });
}
