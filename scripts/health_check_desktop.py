"""Yuyi Desktop 面板体检脚本。"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

print('=' * 60)
print('Yuyi Desktop 面板体检')
print('=' * 60)

# 1. 统计 7 个 tab 实际启用的 widget
print('\n[1] Tab 启用状态 (基于 main_window.py)')
src = open('yuyi_desktop/ui/main_window.py', encoding='utf-8').read()
for tab_key in ('dashboard', 'runtime', 'memory', 'growth',
                'initiative', 'personality', 'settings'):
    real_widget = re.search(
        rf'spec\.key == "{tab_key}".*?tab = (\w+)\(', src, re.S)
    if real_widget:
        print(f'  [{tab_key:12s}] -> {real_widget.group(1)}')
    else:
        print(f'  [{tab_key:12s}] -> _PlaceholderTab (占位)')

# 2. 检查所有 widget 文件
print('\n[2] Widget 文件存在性')
for w in ('dashboard_widget.py', 'runtime_widget.py', 'memory_widget.py',
          'growth_widget.py', 'initiative_widget.py',
          'personality_widget.py', 'control_center_widget.py',
          'settings_widget.py'):
    p = f'yuyi_desktop/ui/widgets/{w}'
    exists = os.path.exists(p)
    size = os.path.getsize(p) if exists else 0
    marker = 'OK' if exists and size > 0 else ('MISSING' if not exists else 'EMPTY')
    print(f'  {w:30s} {marker:10s} {size:>6d} bytes')

# 3. 编译检查
print('\n[3] Widget 编译检查')
import py_compile
for w in ('dashboard_widget.py', 'runtime_widget.py', 'memory_widget.py',
          'growth_widget.py', 'initiative_widget.py',
          'personality_widget.py', 'control_center_widget.py'):
    p = f'yuyi_desktop/ui/widgets/{w}'
    try:
        py_compile.compile(p, doraise=True)
        print(f'  {w:30s} COMPILE_OK')
    except py_compile.PyCompileError as e:
        print(f'  {w:30s} COMPILE_FAIL: {e}')

# 4. 检查所有 widget 是否正确接入 AsyncRefresher
print('\n[4] AsyncRefresher 接入检查')
for w in ('dashboard_widget.py', 'runtime_widget.py', 'memory_widget.py',
          'growth_widget.py', 'initiative_widget.py',
          'personality_widget.py', 'control_center_widget.py'):
    src = open(f'yuyi_desktop/ui/widgets/{w}', encoding='utf-8').read()
    has_refresher = 'AsyncRefresher' in src
    has_worker = '_fetch_in_worker' in src
    has_qtimer_net = (
        'QTimer.timeout.connect(self.refresh' in src
        or 'QTimer.timeout.connect(self._refresh' in src
    )
    has_requests = 'import requests' in src
    has_httpx = 'import httpx' in src
    issues = []
    if not has_refresher:
        issues.append('NO_ASYNC_REFRESHER')
    if not has_worker:
        issues.append('NO_FETCH_IN_WORKER')
    if has_qtimer_net:
        issues.append('QTIMER_DIRECT_NET')
    if has_requests:
        issues.append('REQUESTS_IMPORT')
    if has_httpx:
        issues.append('HTTPX_IMPORT')
    status = ' / '.join(issues) if issues else 'OK'
    print(f'  {w:30s} {status}')

# 5. 检查 widget 是否仍引用 v2 端点
# Phase D.2.11 修复: 之前用 src.count('/api/dashboard/v2') 会把 docstring/注释里
# 的 v2 路径也当作代码调用, 导致误报. 现在用 AST 解析, 仅检测实际字符串字面量
# (非 docstring, 非注释).
print('\n[5] 残留 /api/dashboard/v2/* 端点检查 (AST 精确检测)')
import ast

def _is_docstring_node(node: ast.AST, parent: ast.AST) -> bool:
    """判断节点是否是某个作用域(模块/类/函数)的 docstring。

    docstring 在 AST 里表现为: 作用域 body 列表的第一个 Expr 节点,
    且 Expr.value 是 ast.Constant(str).
    """
    if not isinstance(node, ast.Expr):
        return False
    if not isinstance(node.value, ast.Constant):
        return False
    if not isinstance(node.value.value, str):
        return False
    # 必须是父作用域 body 的第一个元素
    body = getattr(parent, 'body', None)
    if not isinstance(body, list) or not body:
        return False
    return body[0] is node


for w in ('dashboard_widget.py', 'runtime_widget.py', 'memory_widget.py',
          'growth_widget.py', 'initiative_widget.py',
          'personality_widget.py', 'control_center_widget.py',
          'settings_widget.py'):
    p = f'yuyi_desktop/ui/widgets/{w}'
    if not os.path.exists(p):
        continue
    src = open(p, encoding='utf-8').read()
    v2_strings: list = []
    try:
        tree = ast.parse(src)
        # 收集所有 docstring 节点
        docstring_ids: set = set()
        for parent in ast.walk(tree):
            body = getattr(parent, 'body', None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstring_ids.add(id(first))
                    docstring_ids.add(id(first.value))
        # 遍历字符串常量, 排除 docstring
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_ids:
                    continue
                if '/api/dashboard/v2' in node.value:
                    v2_strings.append((node.lineno, node.value[:80]))
    except SyntaxError as e:
        print(f'  {w:30s} PARSE_ERROR: {e}')
        continue
    if v2_strings:
        for lineno, snippet in v2_strings:
            print(f'  {w:30s} 残留 v2 (line {lineno}): {snippet}')
    else:
        print(f'  {w:30s} OK (代码层无残留)')

# 6. control_center_widget 状态
print('\n[6] ControlCenterWidget 状态')
src_cc = open('yuyi_desktop/ui/widgets/control_center_widget.py',
              encoding='utf-8').read()
src_mw = open('yuyi_desktop/ui/main_window.py', encoding='utf-8').read()
in_main = 'ControlCenterWidget' in src_mw
print(f'  control_center_widget.py 存在: {os.path.exists("yuyi_desktop/ui/widgets/control_center_widget.py")}')
print(f'  main_window.py 引用: {in_main}')
if not in_main:
    print('  [WARN] ControlCenterWidget 已实现但未挂到任何 Tab')

# 7. Personality / Growth / Initiative widgets 端点一致性检查
# Phase D.2.11 修复: 改用 AST 检测 service.get_overview 实际函数调用, 避免
# 误把 docstring 里的同名字符串当作代码调用.
print('\n[7] 业务 widget 端点一致性 (AST 精确检测)')
import ast as _ast
for w in ('growth_widget.py', 'initiative_widget.py',
          'personality_widget.py'):
    p = f'yuyi_desktop/ui/widgets/{w}'
    if not os.path.exists(p):
        continue
    src = open(p, encoding='utf-8').read()
    has_service_call = False
    has_v2_string = False
    try:
        tree = _ast.parse(src)
        # 收集所有 docstring 节点 id, 排除
        docstring_ids: set = set()
        for parent in _ast.walk(tree):
            body = getattr(parent, 'body', None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, _ast.Expr)
                        and isinstance(first.value, _ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstring_ids.add(id(first))
                    docstring_ids.add(id(first.value))
        for node in _ast.walk(tree):
            # 检测 self._service.get_overview() 这种实际方法调用
            if isinstance(node, _ast.Call):
                func = node.func
                if isinstance(func, _ast.Attribute) and func.attr == 'get_overview':
                    if isinstance(func.value, _ast.Attribute) and func.value.attr == '_service':
                        has_service_call = True
            # 检测 v2 字符串字面量(排除 docstring)
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_ids:
                    continue
                if '/api/dashboard/v2' in node.value:
                    has_v2_string = True
    except SyntaxError as e:
        print(f'  {w:30s} PARSE_ERROR: {e}')
        continue
    if has_service_call and not has_v2_string:
        print(f'  {w:30s} OK (走 service, 无 v2 字符串)')
    elif has_v2_string:
        print(f'  {w:30s} WARN (代码层含 v2 字符串)')
    else:
        print(f'  {w:30s} UNKNOWN (未检测到 service.get_overview 调用)')

print('\n' + '=' * 60)
print('体检完成')
print('=' * 60)
