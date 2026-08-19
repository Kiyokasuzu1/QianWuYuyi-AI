// Phase 7.4.1 修复验证：模拟 DOM 环境,跑 router.js + index.html 内联 JS
const fs = require('fs');
const path = require('path');

const ROOT = 'd:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI';
const STATIC_DIR = path.join(ROOT, 'static', 'admin', 'dashboard_v2');

// ========== 模拟 DOM 环境 ==========
function makeElement(tag) {
  const el = {
    tagName: tag,
    children: [],
    attributes: {},
    style: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c, force) {
        if (force === true) this._set.add(c);
        else if (force === false) this._set.delete(c);
        else if (this._set.has(c)) this._set.delete(c);
        else this._set.add(c);
        return this._set.has(c);
      },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(type, fn) { this._events = this._events || {}; this._events[type] = this._events[type] || []; this._events[type].push(fn); },
    dispatchEvent(evt) {
      this._events = this._events || {};
      (this._events[evt.type] || []).forEach(fn => fn(evt));
    },
  };
  return el;
}

const allElements = [];

function makePage(id, display) {
  const el = makeElement('section');
  el.id = id;
  el.setAttribute('data-page-id', id.replace('yuyi', '').replace('Page', '').toLowerCase());
  if (display) el.style.display = display;
  allElements.push(el);
  return el;
}

function makeNavItem(route, label, isActive) {
  const el = makeElement('a');
  el.setAttribute('data-route', route);
  el.setAttribute('href', '#' + route);
  if (isActive) el.classList.add('is-active');
  el.textContent = label;
  allElements.push(el);
  return el;
}

// 构造 8 个 page
const pages = {
  overview: makePage('yuyiOverviewPage'),
  runtime: makePage('yuyiRuntimePage', 'none'),
  selfmodel: makePage('yuyiSelfmodelPage', 'none'),
  memory: makePage('yuyiMemoryPage', 'none'),
  reflection: makePage('yuyiReflectionPage', 'none'),
  goal: makePage('yuyiGoalPage', 'none'),
  initiative: makePage('yuyiInitiativePage', 'none'),
  lifeGraph: makePage('yuyiLifeGraphPage', 'none'),
};

// 构造 11 个 nav items(模拟 index.html)
const navItems = [
  makeNavItem('/overview', '生命总览', true),
  makeNavItem('/memory', '记忆'),
  makeNavItem('/emotion', '情绪'),
  makeNavItem('/growth', '成长'),
  makeNavItem('/relationship', '关系'),
  makeNavItem('/goal', '目标'),
  makeNavItem('/initiative', '主动性'),
  makeNavItem('/life-graph', '生命关系'),
  makeNavItem('/reflection', '反思'),
  makeNavItem('/runtime', 'Runtime'),
  makeNavItem('/l2d', 'Live2D'),
];

// 模拟 window/document
const hashListeners = [];
const window_ = {
  location: { hash: '#/overview' },
  YuyiDashboardRouter: null,
  addEventListener(type, fn) {
    if (type === 'hashchange') hashListeners.push(fn);
  },
};

const document_ = {
  querySelectorAll(sel) {
    if (sel === '.yuyi-page') return Object.values(pages);
    if (sel === '.yuyi-nav-item') return navItems;
    return [];
  },
  getElementById(id) {
    return Object.values(pages).find(p => p.id === id) || null;
  },
};

global.window = window_;
global.document = document_;
global.location = window_.location;

// ========== 跑 router.js ==========
const routerCode = fs.readFileSync(path.join(STATIC_DIR, 'js', 'router.js'), 'utf8');
// IIFE 注入
const fn = new Function('window', 'document', 'location', routerCode + '; return window.YuyiDashboardRouter;');
const Router = fn(window_, document_, window_.location);
console.log('[1] YuyiDashboardRouter 已加载:', !!Router);
console.log('    parse():', Router.parse());
console.log('    hashchange 监听器数量(注册前):', hashListeners.length);

// ========== 跑 index.html 的新增内联 JS(启用路由) ==========
const indexHtml = fs.readFileSync(path.join(STATIC_DIR, 'index.html'), 'utf8');
// 提取我们新增的 5 行
const newCode = `
  if (window.YuyiDashboardRouter && typeof window.YuyiDashboardRouter.onChange === "function") {
    window.YuyiDashboardRouter.onChange();
  }
`;
const fn2 = new Function('window', 'document', 'location', newCode);
fn2(window_, document_, window_.location);

console.log('[2] 调用 onChange() 后');
console.log('    hashchange 监听器数量(注册后):', hashListeners.length);
console.log('    overview.is-active:', navItems[0].classList.contains('is-active'));
console.log('    overview display:', pages.overview.style.display || '(空字符串=显示)');

// ========== 模拟点击"目标"==========
console.log('\n[3] 模拟点击"目标" → URL hash 变 #/goal');
window_.location.hash = '#/goal';
hashListeners.forEach(fn => fn({ type: 'hashchange' }));

// 检查状态
console.log('    目标菜单 is-active:', navItems[5].classList.contains('is-active'));
console.log('    overview 菜单 is-active:', navItems[0].classList.contains('is-active'));
console.log('    overview page display:', pages.overview.style.display || '(空字符串=显示)');
console.log('    goal page display:', pages.goal.style.display || '(空字符串=显示)');

// ========== 模拟点击"情绪"(fallback 路由)==========
console.log('\n[4] 模拟点击"情绪" → URL hash 变 #/emotion (不在 PAGES,fallback)');
window_.location.hash = '#/emotion';
hashListeners.forEach(fn => fn({ type: 'hashchange' }));

console.log('    overview 菜单 is-active:', navItems[0].classList.contains('is-active'));
console.log('    情绪菜单 is-active:', navItems[2].classList.contains('is-active'));
console.log('    overview page display:', pages.overview.style.display || '(空字符串=显示)');
console.log('    goal page display:', pages.goal.style.display || '(空字符串=显示)');

// ========== 模拟点击"Runtime"==========
console.log('\n[5] 模拟点击"Runtime" → URL hash 变 #/runtime');
window_.location.hash = '#/runtime';
hashListeners.forEach(fn => fn({ type: 'hashchange' }));

console.log('    Runtime 菜单 is-active:', navItems[9].classList.contains('is-active'));
console.log('    overview 菜单 is-active:', navItems[0].classList.contains('is-active'));
console.log('    runtime page display:', pages.runtime.style.display || '(空字符串=显示)');
console.log('    overview page display:', pages.overview.style.display);
