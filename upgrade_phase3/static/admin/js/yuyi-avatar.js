/* ========================================================
   Yuyi Avatar Interface
   ========================================================
   角色渲染的统一接口层。
   底层可以是 SVG、Live2D 或其他实现，业务代码只与本接口交互。
   当前默认使用 SVG 适配器，未来可无缝切换到 Live2D。
   ======================================================== */

const YuyiAvatar = (function () {
  "use strict";

  let currentAdapter = null;

  /* ============ 适配器接口规范 ============ */
  const AdapterInterface = {
    setEmotion: "function",
    setEnergy: "function",
    setMessage: "function",
    setActivity: "function",
    speak: "function",
    playAction: "function",
    setIdle: "function",
    getType: "function",
  };

  /* ============ SVG 适配器 ============ */
  const SVGAdapter = {
    name: "SVGAdapter",

    init() {
      console.log("[YuyiAvatar] SVG Adapter initialized");
    },

    setEmotion(emotion) {
      if (typeof YuyiCharacter !== "undefined") {
        YuyiCharacter.setExpression(emotion);
      }
    },

    setEnergy(energy) {
      if (typeof YuyiCharacter !== "undefined") {
        YuyiCharacter.setEnergy(energy);
      }
    },

    setMessage(message) {
      if (typeof YuyiCharacter !== "undefined") {
        YuyiCharacter.setMessage(message);
      }
    },

    setActivity(activity, task) {
      if (typeof YuyiCharacter !== "undefined") {
        YuyiCharacter.setActivity(activity, task);
      }
    },

    speak(text, emotion) {
      this.setMessage(text);
      if (emotion) this.setEmotion(emotion);
      this.playAction("speak");
    },

    playAction(action) {
      const characterEl = document.querySelector(".yuyi-character");
      if (!characterEl) return;

      switch (action) {
        case "speak":
          characterEl.style.animation = "yuyi-talking 0.3s ease-in-out";
          setTimeout(() => { characterEl.style.animation = ""; }, 300);
          break;
        case "wave":
          characterEl.style.transform = "rotate(-5deg)";
          setTimeout(() => { characterEl.style.transform = ""; }, 500);
          break;
        case "nod":
          characterEl.style.transform = "translateY(-3px)";
          setTimeout(() => { characterEl.style.transform = ""; }, 300);
          break;
        case "shake":
          characterEl.style.animation = "shake 0.4s ease-in-out";
          setTimeout(() => { characterEl.style.animation = ""; }, 400);
          break;
        case "bounce":
          characterEl.style.animation = "bounce 0.5s ease";
          setTimeout(() => { characterEl.style.animation = ""; }, 500);
          break;
        case "think":
          characterEl.style.animation = "yuyi-think 1.5s ease-in-out";
          break;
        case "sleep":
          characterEl.style.animation = "yuyi-sleep 3s ease-in-out infinite";
          break;
        default:
          break;
      }
    },

    setIdle() {
      this.setEmotion("smile");
      this.setActivity("idle");
    },

    getType() {
      return "svg";
    },
  };

  /* ============ Live2D 适配器 (占位) ============ */
  const Live2DAdapter = {
    name: "Live2DAdapter",

    init() {
      console.log("[YuyiAvatar] Live2D Adapter initialized (stub)");
    },

    setEmotion(emotion) {
      // TODO: 映射到 Live2D 表情 ID
      console.log(`[Live2D] setEmotion(${emotion})`);
    },

    setEnergy(energy) {
      console.log(`[Live2D] setEnergy(${energy})`);
    },

    setMessage(message) {
      console.log(`[Live2D] setMessage(${message})`);
    },

    setActivity(activity, task) {
      console.log(`[Live2D] setActivity(${activity})`);
    },

    speak(text, emotion) {
      console.log(`[Live2D] speak("${text}", emotion: ${emotion})`);
    },

    playAction(action) {
      // TODO: 映射到 Live2D 动作 ID
      console.log(`[Live2D] playAction(${action})`);
    },

    setIdle() {
      console.log("[Live2D] setIdle()");
    },

    getType() {
      return "live2d";
    },
  };

  /* ============ 引擎 ============ */

  function useAdapter(adapter) {
    if (typeof adapter !== "object") {
      throw new Error("[YuyiAvatar] 适配器必须是对象");
    }
    Object.keys(AdapterInterface).forEach(method => {
      if (typeof adapter[method] !== "function") {
        throw new Error(`[YuyiAvatar] 适配器缺少方法: ${method}`);
      }
    });
    currentAdapter = adapter;
    currentAdapter.init();
    return currentAdapter;
  }

  function getCurrentAdapter() {
    return currentAdapter;
  }

  function createSVGAdapter() {
    return useAdapter(SVGAdapter);
  }

  function createLive2DAdapter() {
    return useAdapter(Live2DAdapter);
  }

  /* 代理方法：统一转发给当前适配器 */

  function setEmotion(emotion) {
    return currentAdapter?.setEmotion(emotion);
  }

  function setEnergy(energy) {
    return currentAdapter?.setEnergy(energy);
  }

  function setMessage(message) {
    return currentAdapter?.setMessage(message);
  }

  function setActivity(activity, task) {
    return currentAdapter?.setActivity(activity, task);
  }

  function speak(text, emotion) {
    return currentAdapter?.speak(text, emotion);
  }

  function playAction(action) {
    return currentAdapter?.playAction(action);
  }

  function setIdle() {
    return currentAdapter?.setIdle();
  }

  function getType() {
    return currentAdapter?.getType();
  }

  return {
    useAdapter,
    getCurrentAdapter,
    createSVGAdapter,
    createLive2DAdapter,
    setEmotion,
    setEnergy,
    setMessage,
    setActivity,
    speak,
    playAction,
    setIdle,
    getType,
    SVGAdapter,
    Live2DAdapter,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiAvatar = YuyiAvatar;
}
