"""Phase D.2.10: 3 widget v2 health 端点修复端到端冒烟脚本。"""
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

# 1) 端点常量统一性
print('=' * 60)
print('Phase D.2.10: 3 widget v2 health 端点修复验证')
print('=' * 60)

from yuyi_desktop.ui.widgets.growth_widget import ENDPOINT_HEALTH as GH
from yuyi_desktop.ui.widgets.initiative_widget import ENDPOINT_HEALTH as IH
from yuyi_desktop.ui.widgets.personality_widget import ENDPOINT_HEALTH as PH
from yuyi_desktop.ui.widgets.memory_widget import ENDPOINT_HEALTH as MH
from yuyi_desktop.ui.widgets.runtime_widget import ENDPOINT_HEALTH as RH

print('\n[1] 5 widget 端点常量统一性:')
all_endpoints = {'growth': GH, 'initiative': IH, 'personality': PH,
                 'memory': MH, 'runtime': RH}
for k, v in all_endpoints.items():
    status = 'OK' if v == '/health' else 'FAIL'
    print(f'  {k:12s} ENDPOINT_HEALTH = {v} [{status}]')

assert all(v == '/health' for v in all_endpoints.values()), '端点不一致'
print('  ✓ 全部统一为 /health')

# 2) Mock 数据流测试
print('\n[2] _fetch_in_worker 端点调用检查:')
for widget_name in ('growth_widget', 'initiative_widget', 'personality_widget'):
    mod = __import__(f'yuyi_desktop.ui.widgets.{widget_name}', fromlist=[widget_name])
    cls_name = widget_name.replace('_widget', '').capitalize() + 'Widget'
    cls = getattr(mod, cls_name)
    import inspect
    src = inspect.getsource(cls._fetch_in_worker)
    has_get = 'self._api.get(ENDPOINT_HEALTH)' in src
    has_get_path = 'self._api.get_path(ENDPOINT_HEALTH)' in src
    status = 'OK' if (has_get and not has_get_path) else 'FAIL'
    print(f'  {widget_name:20s} _api.get=✓ _api.get_path=✗ [{status}]')

# 3) 数据流模拟
print('\n[3] 3 widget _on_data_ready 端到端:')
from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
from yuyi_desktop.ui.widgets.initiative_widget import InitiativeWidget

# Growth
stub = GrowthWidget.__new__(GrowthWidget)
stub._api = MagicMock()
stub._service = MagicMock()
stub._last_data = None
stub._render_data = MagicMock()
stub._render_error = MagicMock()
result_g = {
    'overview': {'available': True, 'degraded': False, 'total': 50},
    'health': {'success': True, 'latency_ms': 80.0},
}
stub._on_data_ready(result_g)
ok = stub._render_data.called and not stub._render_error.called
print(f'  growth     : render_data={stub._render_data.called} '
      f'render_error={stub._render_error.called} [{"OK" if ok else "FAIL"}]')

# Initiative
stub = InitiativeWidget.__new__(InitiativeWidget)
stub._api = MagicMock()
stub._service = MagicMock()
stub._last_data = None
stub._render_data = MagicMock()
stub._render_error = MagicMock()
result_i = {
    'overview': {'available': True, 'degraded': False, 'total': 20},
    'health': {'success': True, 'latency_ms': 60.0},
}
stub._on_data_ready(result_i)
ok = stub._render_data.called and not stub._render_error.called
print(f'  initiative : render_data={stub._render_data.called} '
      f'render_error={stub._render_error.called} [{"OK" if ok else "FAIL"}]')

# Personality
from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
stub = PersonalityWidget.__new__(PersonalityWidget)
for attr in (
    '_traits_detail', '_evolution_detail', '_growth_detail', '_beliefs_detail',
    '_traits_group_expanded', '_beliefs_group_expanded',
    '_evolution_group_expanded', '_growth_group_expanded',
):
    setattr(stub, attr, False)
for attr in ('_traits_list', '_beliefs_list', '_evolution_list', '_growth_list'):
    mock_list = MagicMock()
    mock_list.clear = MagicMock()
    mock_list.addItem = MagicMock()
    setattr(stub, attr, mock_list)
stub._render_from_fetch_result = MagicMock()
stub._render_traits_detail = MagicMock()
stub._render_beliefs_detail = MagicMock()
stub._render_evolution_detail = MagicMock()
stub._render_growth_detail = MagicMock()
stub._extract_growth_history = MagicMock(return_value=[])
stub._extract_beliefs = MagicMock(return_value=[])
stub._render_error = MagicMock()
data_p = {
    'overview': {'available': True, 'selfmodel_v2': {}},
    'snapshot': {},
    'traits': [],
    'selfmodel': {},
    'evolution': [],
    'growth_history': [],
    'health': {'success': True, 'latency_ms': 100.0},
}
stub._on_data_ready(data_p)
ok = (stub._render_from_fetch_result.called and not stub._render_error.called)
print(f'  personality: render_from_fetch={stub._render_from_fetch_result.called} '
      f'render_error={stub._render_error.called} [{"OK" if ok else "FAIL"}]')

# 4) 关键回归:即使 v2 health 403,3 widget 仍走 service 路径
print('\n[4] 关键回归: v2 health 403 模拟:')

# 模拟 health 端点 403 的情况
stub = GrowthWidget.__new__(GrowthWidget)
stub._api = MagicMock()
stub._service = MagicMock()
stub._last_data = None
stub._render_data = MagicMock()
stub._render_error = MagicMock()
result_403 = {
    'overview': {'available': True, 'degraded': False, 'total': 100},
    'health': {
        'success': False,
        'error': 'auth_error: HTTP 403',
        'latency_ms': 0.0,
    },
}
stub._on_data_ready(result_403)
# 关键: service 仍走通 (因为 service_available=True)
ok = stub._render_data.called and not stub._render_error.called
print(f'  growth: v2 403 模拟,service 数据保留: '
      f'render_data={stub._render_data.called} '
      f'render_error={stub._render_error.called} [{"OK" if ok else "FAIL"}]')

print('\n' + '=' * 60)
print('冒烟测试完成')
print('=' * 60)
