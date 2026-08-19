import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

# 把仓库根加到 sys.path,避免 ModuleNotFoundError
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

# Mock service
from yuyi_desktop.services.memory_service import MemoryService
mock_service = MagicMock(spec=MemoryService)
mock_service.get_overview.return_value = {
    'total': 316,
    'important': 50,
    'v2': {
        'total_count': 316,
        'important_count': 50,
        'recent_count': 12,
        'last_update': '2026-08-06T10:00:00',
        'by_type': {
            'user_fact': 100, 'user_preference': 30,
            'user_event': 20, 'user_relationship': 10,
            'user_milestone': 5, 'user_experience': 15,
        },
        'by_category': {'normal_user': 150, 'system_pollution': 0, 'invalid': 0},
        'quality': {'status': 'healthy'},
    },
    'available': True,
    'degraded': False,
}

# Mock api client
mock_api = MagicMock()
mock_api.get.return_value = {'success': True, 'latency_ms': 80.0, 'schema_version': '1.0'}

# 构造 widget
from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
with patch('yuyi_desktop.ui.widgets.memory_widget.get_memory_service', return_value=mock_service), \
     patch('yuyi_desktop.ui.widgets.memory_widget.get_api_client', return_value=mock_api):
    widget = MemoryWidget()

# 验证 _fetch_in_worker 调用
result = widget._fetch_in_worker()
print('fetch_in_worker result keys:', sorted(result.keys()))
print('overview.total:', result['overview'].get('total'))
print('health_env.success:', result['health_env'].get('success'))

# 验证 _on_data_ready 渲染
def fake_render_data(data, source):
    src = data.get('source', '?')
    total = data.get('total')
    online = data.get('online')
    print(f'_render_data: source={src}, total={total}, online={online}')

def fake_render_error(msg, hint=''):
    print(f'_render_error: {msg} ({hint})')

widget._render_data = fake_render_data
widget._render_error = fake_render_error
widget._on_data_ready(result)

# 验证 _on_data_failed
widget._on_data_failed('test error')

# 验证 错误路径 (service 不可用 + health 失败)
result_bad = {
    'overview': {'available': False, 'degraded': True, 'total': 0, 'error': 'connection_error'},
    'health_env': {'success': False, 'error': 'timeout', 'latency_ms': 5000.0},
}
print('--- Bad path ---')
widget._on_data_ready(result_bad)

print('SMOKE_TEST_OK')
