/* ========================================================
   Yuyi Character Controller (增强版)
   ========================================================
   统一角色状态控制 API。
   当前驱动 SVG 角色，未来可切换到 Live2D / 3D Avatar。
   新增：自动眨眼、呼吸光效、更多表情、眨眼动画。
   业务代码只需调用 setState()，无需关心底层实现。
   ======================================================== */

const YuyiCharacter = (function () {
  "use strict";

  /* 表情 → SVG 路径映射 */
  const EXPRESSION_MAP = {
    smile: {
      eyes: "M48 60 Q50 56 52 60",
      eyesRight: "M68 60 Q70 56 72 60",
      mouth: "M55 74 Q60 78 65 74",
    },
    happy: {
      eyes: "M48 58 Q50 54 52 58",
      eyesRight: "M68 58 Q70 54 72 58",
      mouth: "M53 72 Q60 82 67 72",
    },
    excited: {
      eyes: "M48 57 Q52 53 52 58",
      eyesRight: "M68 57 Q72 53 72 58",
      mouth: "M52 71 Q60 84 68 71",
    },
    sad: {
      eyes: "M48 62 Q50 66 52 62",
      eyesRight: "M68 62 Q70 66 72 62",
      mouth: "M55 77 Q60 73 65 77",
    },
    angry: {
      eyes: "M46 58 L54 62",
      eyesRight: "M66 62 L74 58",
      mouth: "M55 76 Q60 72 65 76",
    },
    surprised: {
      eyes: "M50 58 m-4 0 a4 4 0 1 0 8 0 a4 4 0 1 0 -8 0",
      eyesRight: "M70 58 m-4 0 a4 4 0 1 0 8 0 a4 4 0 1 0 -8 0",
      mouth: "M57 75 a3 3 0 1 0 6 0 a3 3 0 1 0 -6 0",
    },
    fearful: {
      eyes: "M50 58 m-3 0 a3 3 0 1 0 6 0 a3 3 0 1 0 -6 0",
      eyesRight: "M70 58 m-3 0 a3 3 0 1 0 6 0 a3 3 0 1 0 -6 0",
      mouth: "M56 75 Q60 73 64 75",
    },
    worried: {
      eyes: "M48 61 Q50 57 52 61",
      eyesRight: "M68 61 Q70 57 72 61",
      mouth: "M55 76 Q60 74 65 76",
    },
    dazed: {
      eyes: "M48 58 Q50 60 52 58",
      eyesRight: "M68 58 Q70 60 72 58",
      mouth: "M58 75 Q60 73 62 75",
    },
    sleepy: {
      eyes: "M48 60 Q50 61 52 60",
      eyesRight: "M68 60 Q70 61 72 60",
      mouth: "M58 76 a2 2 0 1 0 4 0 a2 2 0 1 0 -4 0",
    },
    curious: {
      eyes: "M48 58 Q52 54 52 60",
      eyesRight: "M68 58 Q72 54 72 60",
      mouth: "M55 75 Q60 77 65 75",
    },
  };

  /* 表情 → 情绪指示器 emoji */
  const EXPRESSION_EMOJI = {
    smile:    "😊",
    happy:    "✨",
    excited:  "🎉",
    sad:      "😢",
    angry:    "😠",
    surprised:"😲",
    fearful:  "😰",
    worried:  "😟",
    dazed:    "😵",
    sleepy:   "💤",
    curious:  "🤔",
  };

  /* 情绪 → 背景/光效映射 */
  const MOOD_MAP = {
    smile:    { mood: "calm",     particle: "normal", filter: "" },
    happy:    { mood: "happy",    particle: "sparkle", filter: "hue-rotate(-10deg)" },
    excited:  { mood: "excited",  particle: "burst",   filter: "hue-rotate(-15deg) saturate(1.2)" },
    sad:      { mood: "sad",      particle: "slow",    filter: "hue-rotate(20deg) brightness(0.95)" },
    angry:    { mood: "angry",    particle: "flicker", filter: "hue-rotate(30deg) saturate(0.8)" },
    surprised:{ mood: "surprised", particle: "burst",   filter: "hue-rotate(-5deg) brightness(1.05)" },
    fearful:  { mood: "fearful",  particle: "slow",    filter: "hue-rotate(15deg) brightness(0.9)" },
    worried:  { mood: "worried",  particle: "normal",  filter: "hue-rotate(10deg)" },
    dazed:    { mood: "calm",     particle: "normal",  filter: "hue-rotate(5deg)" },
    sleepy:   { mood: "sad",      particle: "slow",    filter: "hue-rotate(15deg) brightness(0.95)" },
    curious:  { mood: "surprised", particle: "sparkle", filter: "hue-rotate(-5deg) brightness(1.02)" },
  };

  const VALID_EXPRESSIONS = Object.keys(EXPRESSION_MAP);

  let currentState = {
    emotion: "平静",
    expression: "smile",
    message: "正在为您待命~",
    energy: 50,
    attention: 80,
    activity: "idle",
    current_task: "",
  };

  let listeners = [];
  let blinkTimer = null;
  let breatheTimer = null;
  let isBlinking = false;

  /* ============ 公开 API ============ */

  function setState(newState) {
    const prev = { ...currentState };
    currentState = { ...currentState, ...newState };

    renderExpression(currentState.expression);
    renderEnergy(currentState.energy);
    renderMessage(currentState.message);
    renderGlow(currentState.energy, currentState.expression);
    renderMood(currentState.expression);

    notifyListeners("change", { prev, current: { ...currentState } });
  }

  function getState() {
    return { ...currentState };
  }

  function setExpression(expression) {
    if (!VALID_EXPRESSIONS.includes(expression)) {
      console.warn(`[YuyiCharacter] 未知表情: ${expression}`);
      return;
    }
    setState({ expression });
  }

  function setEnergy(energy) {
    const e = Math.max(0, Math.min(100, energy));
    setState({ energy: e });
  }

  function setMessage(message) {
    setState({ message });
  }

  function setActivity(activity, task = "") {
    setState({ activity, current_task: task });
  }

  function on(event, callback) {
    listeners.push({ event, callback });
  }

  /* ============ 自动眨眼系统 ============ */

  function startBlinkLoop() {
    if (blinkTimer) clearTimeout(blinkTimer);
    scheduleNextBlink();
  }

  function scheduleNextBlink() {
    const interval = 3000 + Math.random() * 4000; /* 3-7 秒 */
    blinkTimer = setTimeout(() => {
      performBlink();
      scheduleNextBlink();
    }, interval);
  }

  function performBlink() {
    if (isBlinking) return;
    isBlinking = true;

    const leftEye = document.getElementById("char-eye-left");
    const rightEye = document.getElementById("char-eye-right");
    if (!leftEye || !rightEye) { isBlinking = false; return; }

    const originalLeft = leftEye.getAttribute("d");
    const originalRight = rightEye.getAttribute("d");

    /* 闭眼：变成一条线 */
    const blinkPath = "M48 60 L52 60";
    const blinkPathRight = "M68 60 L72 60";

    leftEye.setAttribute("d", blinkPath);
    rightEye.setAttribute("d", blinkPathRight);

    setTimeout(() => {
      leftEye.setAttribute("d", originalLeft);
      rightEye.setAttribute("d", originalRight);
      isBlinking = false;
    }, 150);
  }

  /* ============ 呼吸光效系统 ============ */

  function startBreatheLoop() {
    if (breatheTimer) clearInterval(breatheTimer);
    breatheTimer = setInterval(() => {
      const characterEl = document.querySelector(".yuyi-character");
      if (!characterEl) return;
      const mood = MOOD_MAP[currentState.expression] || MOOD_MAP.smile;
      const glowColor = getComputedGlowColor(mood.mood);
      const intensity = Math.max(0.3, currentState.energy / 100);
      const breathe = 0.8 + 0.2 * Math.sin(Date.now() / 800);
      characterEl.style.filter = `drop-shadow(0 0 ${8 * intensity * breathe}px ${glowColor})`;
    }, 100);
  }

  function stopBreatheLoop() {
    if (breatheTimer) {
      clearInterval(breatheTimer);
      breatheTimer = null;
    }
  }

  /* ============ 渲染层（SVG 驱动） ============ */

  function renderExpression(expression) {
    const face = EXPRESSION_MAP[expression] || EXPRESSION_MAP.smile;

    const leftEye = document.getElementById("char-eye-left");
    const rightEye = document.getElementById("char-eye-right");
    const mouth = document.getElementById("char-mouth");

    if (leftEye) leftEye.setAttribute("d", face.eyes);
    if (rightEye) rightEye.setAttribute("d", face.eyesRight);
    if (mouth) mouth.setAttribute("d", face.mouth);

    /* 更新情绪指示器 emoji */
    const moodEmoji = document.getElementById("mood-emoji");
    if (moodEmoji) {
      moodEmoji.textContent = EXPRESSION_EMOJI[expression] || "✦";
    }
  }

  function renderEnergy(energy) {
    const energyBar = document.getElementById("character-energy-bar");
    const energyLabel = document.getElementById("character-energy-label");
    if (energyBar) {
      energyBar.style.transition = "width 0.6s cubic-bezier(0.34, 1.56, 0.64, 1)";
      energyBar.style.width = `${energy}%`;
    }
    if (energyLabel) {
      energyLabel.textContent = `能量 ${energy}%`;
    }
  }

  function renderMessage(message) {
    const msgEl = document.getElementById("character-message");
    if (!msgEl) return;

    msgEl.style.opacity = "0";
    setTimeout(() => {
      msgEl.textContent = message;
      msgEl.style.transition = "opacity 0.4s cubic-bezier(0.4, 0, 0.2, 1)";
      msgEl.style.opacity = "1";
    }, 200);
  }

  function renderGlow(energy, expression) {
    const characterEl = document.querySelector(".yuyi-character");
    if (!characterEl) return;

    const intensity = Math.max(0.3, energy / 100);
    const mood = MOOD_MAP[expression] || MOOD_MAP.smile;
    const glowColor = getComputedGlowColor(mood.mood);

    characterEl.style.filter = `drop-shadow(0 0 ${8 * intensity}px ${glowColor})`;
  }

  function renderMood(expression) {
    const scene = document.querySelector(".yuyi-scene");
    if (!scene) return;

    const mood = MOOD_MAP[expression] || MOOD_MAP.smile;

    scene.style.transition = "filter 0.8s cubic-bezier(0.4, 0, 0.2, 1)";
    scene.style.filter = mood.filter || "";

    if (typeof YuyiBackground !== "undefined") {
      YuyiBackground.setMood(mood.mood, mood.particle);
    }

    if (typeof YuyiThemeManager !== "undefined") {
      YuyiThemeManager.applyMoodOverride(mood.mood);
    }
  }

  function getComputedGlowColor(mood) {
    const map = {
      calm: "rgba(104, 144, 255, 0.5)",
      happy: "rgba(244, 114, 182, 0.6)",
      excited: "rgba(244, 114, 182, 0.7)",
      sad: "rgba(148, 163, 184, 0.5)",
      angry: "rgba(248, 113, 113, 0.6)",
      surprised: "rgba(167, 139, 250, 0.6)",
      fearful: "rgba(148, 163, 184, 0.5)",
      worried: "rgba(251, 191, 36, 0.5)",
    };
    return map[mood] || map.calm;
  }

  function notifyListeners(event, data) {
    listeners.forEach(l => {
      if (l.event === event) {
        try { l.callback(data); } catch (e) { console.error(e); }
      }
    });
  }

  /* ============ 初始化 ============ */

  function init(initialState) {
    if (initialState) setState(initialState);
    startBlinkLoop();
    startBreatheLoop();
    return currentState;
  }

  return {
    init,
    setState,
    getState,
    setExpression,
    setEnergy,
    setMessage,
    setActivity,
    on,
    VALID_EXPRESSIONS,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiCharacter = YuyiCharacter;
}
