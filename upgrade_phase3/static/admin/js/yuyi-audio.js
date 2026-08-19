/* ========================================================
   Yuyi Audio — Web Audio API 音效反馈
   ========================================================
   使用 Web Audio API 生成轻量级提示音，无需外部音频文件。
   为每种操作（点击、成功、警告、唤醒等）提供不同的音效反馈。
   所有音效都设计得轻柔、不刺耳，符合羽依的温柔风格。
   ======================================================== */

const YuyiAudio = (function () {
  "use strict";

  let audioContext = null;
  let enabled = false;
  let masterVolume = 0.1;

  /* ============ 初始化 ============ */

  function init() {
    try {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      enabled = true;
      console.log("[YuyiAudio] Initialized");
    } catch (e) {
      console.warn("[YuyiAudio] Web Audio API 不可用:", e.message);
      enabled = false;
    }
    return enabled;
  }

  function setEnabled(value) {
    enabled = !!value;
    if (enabled && !audioContext) {
      init();
    }
  }

  function isEnabled() {
    return enabled;
  }

  function setVolume(vol) {
    masterVolume = Math.max(0, Math.min(1, vol));
  }

  /* ============ 核心：音调生成器 ============ */

  function playTone(freq, duration, options = {}) {
    if (!enabled || !audioContext) return;

    const {
      type = "sine",
      volume = masterVolume,
      attack = 0.01,
      release = 0.05,
      freqEnd = null,
      delay = 0,
    } = options;

    const now = audioContext.currentTime + delay;
    const osc = audioContext.createOscillator();
    const gain = audioContext.createGain();

    osc.type = type;
    osc.frequency.setValueAtTime(freq, now);
    if (freqEnd !== null) {
      osc.frequency.exponentialRampToValueAtTime(freqEnd, now + duration);
    }

    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(volume, now + attack);
    gain.gain.linearRampToValueAtTime(0, now + duration + release);

    osc.connect(gain);
    gain.connect(audioContext.destination);

    osc.start(now);
    osc.stop(now + duration + release + 0.05);
  }

  /* ============ 预设音效 ============ */

  const SoundEffects = {
    /* 轻点击 */
    click() {
      playTone(800, 0.03, {
        type: "sine",
        volume: 0.05,
        release: 0.02,
      });
    },

    /* 启动唤醒 */
    wake() {
      if (!enabled || !audioContext) return;
      const now = audioContext.currentTime;
      [440, 554, 659].forEach((freq, i) => {
        playTone(freq, 0.15, {
          type: "sine",
          volume: 0.08,
          release: 0.1,
          delay: i * 0.12,
        });
      });
    },

    /* 成功完成 */
    success() {
      if (!enabled || !audioContext) return;
      [523, 659, 784].forEach((freq, i) => {
        playTone(freq, 0.12, {
          type: "triangle",
          volume: 0.06,
          release: 0.08,
          delay: i * 0.06,
        });
      });
    },

    /* 警告提醒 */
    warning() {
      if (!enabled || !audioContext) return;
      [400, 350, 400].forEach((freq, i) => {
        playTone(freq, 0.15, {
          type: "square",
          volume: 0.04,
          release: 0.1,
          delay: i * 0.15,
        });
      });
    },

    /* 错误 */
    error() {
      if (!enabled || !audioContext) return;
      [300, 250, 200].forEach((freq, i) => {
        playTone(freq, 0.2, {
          type: "sawtooth",
          volume: 0.04,
          release: 0.1,
          delay: i * 0.1,
        });
      });
    },

    /* 关闭/进入睡眠 */
    sleep() {
      if (!enabled || !audioContext) return;
      [659, 523, 392].forEach((freq, i) => {
        playTone(freq, 0.2, {
          type: "sine",
          volume: 0.06,
          release: 0.15,
          delay: i * 0.15,
        });
      });
    },

    /* 通知 */
    notification() {
      playTone(880, 0.08, {
        type: "sine",
        volume: 0.05,
        release: 0.05,
      });
      setTimeout(() => {
        playTone(1100, 0.1, {
          type: "sine",
          volume: 0.05,
          release: 0.08,
        });
      }, 80);
    },

    /* 星尘/魔法感 */
    sparkle() {
      if (!enabled || !audioContext) return;
      [880, 1100, 1320, 1760].forEach((freq, i) => {
        playTone(freq, 0.08, {
          type: "sine",
          volume: 0.04,
          release: 0.1,
          delay: i * 0.05,
        });
      });
    },

    /* 对话式回复 */
    reply(emotion = "calm") {
      const maps = {
        happy: [523, 587, 659],
        excited: [587, 659, 784, 880],
        sad: [392, 349, 330],
        calm: [440, 494, 523],
        worried: [392, 440, 392],
      };
      const notes = maps[emotion] || maps.calm;
      notes.forEach((freq, i) => {
        playTone(freq, 0.1, {
          type: "triangle",
          volume: 0.04,
          release: 0.05,
          delay: i * 0.08,
        });
      });
    },
  };

  /* ============ 便捷调用 ============ */

  function play(effectName) {
    if (SoundEffects[effectName]) {
      SoundEffects[effectName]();
    }
  }

  return {
    init,
    setEnabled,
    isEnabled,
    setVolume,
    play,
    SoundEffects,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiAudio = YuyiAudio;
}
