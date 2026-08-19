/* =============================================================
 * router.js —— Dashboard V2 简易 Hash 路由
 *
 * 当前阶段:
 *  - 仅支持 /overview 一条路由
 *  - 其他路径自动 fallback 到 /overview
 *  - 后续可扩展为多页(SPA)
 * ============================================================= */
(function (global) {
  "use strict";

  const PAGES = {
    "/overview": { title: "生命总览", subtitle: "实时观察羽依核心生命指标" },
    "/runtime-center": { title: "运行中心", subtitle: "羽依运行状态观察中心 · 服务状态 / 生命周期 / 请求链路" },
    "/runtime": { title: "Runtime", subtitle: "Runtime / Lifecycle / Integration 事件" },
    "/selfmodel": { title: "SelfModel", subtitle: "Identity / Traits / Capabilities / Beliefs / Timeline" },
    "/memory": { title: "Memory", subtitle: "近期记忆 / 重要记忆 / 时间线" },
    "/reflection": { title: "Reflection", subtitle: "Reflection 记录 / Insight / Evidence Chain" },
    "/goal": { title: "Goal", subtitle: "当前目标 / 目标列表 / 变化历史" },
    "/initiative": { title: "Initiative", subtitle: "兴趣信号 / 候选行动 / 过滤历史" },
    "/life-graph": { title: "Life Graph", subtitle: "跨域生命关系图(可追溯可解释)" },
  };

  function parseHash() {
    const raw = (location.hash || "").replace(/^#/, "");
    const path = raw.startsWith("/") ? raw : "/" + raw;
    return PAGES[path] ? path : "/overview";
  }

  function applyActive(route) {
    const items = document.querySelectorAll(".yuyi-nav-item");
    items.forEach((el) => {
      const r = el.getAttribute("data-route");
      el.classList.toggle("is-active", r === route);
    });
  }

  function applyTitle(route) {
    const meta = PAGES[route] || PAGES["/overview"];
    const title = document.getElementById("yuyiPageTitle");
    const sub = document.getElementById("yuyiPageSubtitle");
    if (title) title.textContent = meta.title;
    if (sub) sub.textContent = meta.subtitle;
  }

  function showPage(route) {
    const pages = document.querySelectorAll(".yuyi-page");
    pages.forEach((el) => {
      el.style.display = (el.getAttribute("data-page-id") === (route.replace("/", "")) || route === "/overview")
        ? ""
        : "none";
    });
  }

  function onChange(cb) {
    const handler = () => {
      const route = parseHash();
      applyActive(route);
      applyTitle(route);
      showPage(route);
      if (typeof cb === "function") cb(route);
    };
    window.addEventListener("hashchange", handler);
    handler();
    return handler;
  }

  global.YuyiDashboardRouter = {
    parse: parseHash,
    onChange: onChange,
  };
})(window);
