/* ========================================================
   Yuyi Toast Component
   ========================================================
   用法：
   YuyiToast.show("操作成功", { type: "success" })
   YuyiToast.info("提示")
   YuyiToast.success("成功")
   YuyiToast.warning("警告")
   YuyiToast.error("错误")
   ======================================================== */

const YuyiToast = (function () {
  "use strict";

  const ICONS = {
    info: "💫",
    success: "♡",
    warning: "⚠",
    error: "✕",
  };

  const DEFAULT_DURATION = 3000;
  let container = null;

  function ensureContainer() {
    if (container) return container;
    container = document.createElement("div");
    container.className = "yuyi-toast-container";
    document.body.appendChild(container);
    return container;
  }

  function show(message, options = {}) {
    const {
      type = "info",
      title,
      duration = DEFAULT_DURATION,
      closable = true,
    } = options;

    const ctn = ensureContainer();
    const toast = document.createElement("div");
    toast.className = `yuyi-toast yuyi-toast--${type}`;

    const icon = ICONS[type] || "💫";
    const displayTitle = title || defaultTitle(type);

    toast.innerHTML = `
      <span class="yuyi-toast__icon">${icon}</span>
      <div class="yuyi-toast__content">
        ${displayTitle ? `<div class="yuyi-toast__title">${displayTitle}</div>` : ""}
        <div class="yuyi-toast__message">${message}</div>
      </div>
      ${closable ? '<button class="yuyi-toast__close" aria-label="关闭">×</button>' : ""}
    `;

    ctn.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.add("yuyi-toast--show");
    });

    const closeBtn = toast.querySelector(".yuyi-toast__close");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => hide(toast));
    }

    if (duration > 0) {
      setTimeout(() => hide(toast), duration);
    }

    return toast;
  }

  function hide(toast) {
    if (!toast || !toast.parentNode) return;
    toast.classList.remove("yuyi-toast--show");
    toast.classList.add("yuyi-toast--hide");
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }

  function defaultTitle(type) {
    const titles = {
      info: "提示",
      success: "成功",
      warning: "注意",
      error: "出错了",
    };
    return titles[type] || "";
  }

  return {
    show,
    hide,
    info: (msg, opts) => show(msg, { ...opts, type: "info" }),
    success: (msg, opts) => show(msg, { ...opts, type: "success" }),
    warning: (msg, opts) => show(msg, { ...opts, type: "warning" }),
    error: (msg, opts) => show(msg, { ...opts, type: "error" }),
  };
})();

if (typeof window !== "undefined") {
  window.YuyiToast = YuyiToast;
}
