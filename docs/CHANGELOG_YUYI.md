# 浅雾羽依 AI 变更日志 (CHANGELOG_YUYI)

---

## [Phase 4 - 2026-07-29] Token 优化模块审计与断链修复

### 分支
`fix/token-opt-config-loading`（基于 `feature/admin-expansion`）

### 审计发现

#### 1. Token 优化模块位置
- 模块目录：`src/token_opt/`
- 包含文件：`__init__.py`（配置读取 + 导出）、`module.py`（模块声明）、`history_compressor.py`（历史压缩）、`memory_summarizer.py`（记忆摘要）
- 接入点：`src/engine.py` 的 `ResponseEngine.generate()` 在每次聊天请求时检查 `is_token_opt_enabled()`

#### 2. 关键断链（已修复）
- **断链**：`src/token_opt/__init__.py` 尝试导入 `from src.utils.config import get_config`，但 `src/utils/config.py` 不存在（utils 目录只有 `text.py` 和 `time.py`）
- **影响**：`is_token_opt_enabled()` 永远走 except 兜底，只能读环境变量 `YUYI_TOKEN_OPT`，**config.yaml 中的 `token_opt.enabled` 完全读不到**
- **结果**：控制台开关写入 config.yaml 但对运行时无效；engine.py 永远走 `_build_messages_original()`，从不走 `_build_messages_opt()`

#### 3. Token 统计能力
- **完全不存在**：无输入 token 统计、无输出 token 统计、无历史消耗统计、无单次请求消耗、无优化前后对比
- engine.py 调用 DeepSeek API 后未记录 `response.usage`
- 此项属于"新功能"，本次不新增

#### 4. Token 优化策略现状
| 策略 | 代码状态 | 运行时状态 |
|---|---|---|
| 历史压缩（HistoryCompressor） | ✅ 完整 | ❌ 断链导致从未执行 |
| 记忆摘要（MemorySummarizer） | ✅ 完整 | ❌ 断链导致从未执行 |
| 记忆数量限制 | ✅ 硬编码 `chat_memories[:5]` | ✅ 在 original 路径生效 |
| 历史长度限制 | ✅ 硬编码 `history[-20:]` | ✅ 在 original 路径生效 |
| 动态 context 调整 | ❌ 不存在 | — |
| 优先级机制 | ❌ 不存在 | — |

### 修复内容

#### 修改文件列表
| 文件 | 修改类型 | 说明 |
|---|---|---|
| `src/token_opt/__init__.py` | 修复 | 修正 `is_token_opt_enabled()` 的导入路径：`src.utils.config.get_config`（不存在）→ `src.config.load_config`（已存在，带缓存）。新增环境变量优先级逻辑：环境变量 > config.yaml > 默认 False |
| `config.yaml` | 新增段 | 添加 `token_opt.enabled: false` 配置段（默认关闭，保持向后兼容） |
| `tests/test_token_opt.py` | 新增 | 23 个测试用例，覆盖配置读取、历史压缩、记忆摘要、engine 路径切换 |

#### 修复细节
1. **`is_token_opt_enabled()` 优先级**：环境变量 `YUYI_TOKEN_OPT` > `config.yaml: token_opt.enabled` > 默认 `False`
   - 环境变量 `true` → 强制启用（覆盖配置）
   - 环境变量 `false` → 强制禁用（覆盖配置）
   - 环境变量未设置 → 读取 config.yaml
   - 配置读取失败 → 默认 False

2. **向后兼容**：
   - config.yaml 中 `token_opt.enabled` 默认 `false`，不影响现有行为
   - 未设置环境变量且配置段缺失时返回 False
   - 不修改 API Key
   - 不改变 QQ 接入
   - 不影响控制台 UI

### 测试结果
```
tests/test_token_opt.py: 23 passed in 0.14s
tests/test_engine_context.py: 11 passed in 0.06s（回归测试，未破坏）
```

测试覆盖：
- `TestIsTokenOptEnabled`（7 项）：默认值、环境变量优先、配置读取、异常回退
- `TestHistoryCompressor`（6 项）：空历史、短历史不压缩、长历史压缩、摘要插入、降级摘要
- `TestMemorySummarizer`（8 项）：空记忆、重要性过滤、降级保底、排序、数量限制、长度截断、前缀标记、情绪标签
- `TestEngineTokenOptPath`（2 项）：禁用走 original、启用走 opt

### 控制台开关闭环状态

修复后的闭环：
```
控制台 UI 按钮
  ↓ POST /admin/api/module/token_opt/start
routes.py: api_module_start
  ↓ ConfigManager.toggle_module("token_opt", True)
config.yaml: token_opt.enabled: true
  ↓
engine.py: _is_token_opt_enabled()
  ↓ is_token_opt_enabled()
  ↓ src.config.load_config() ← 修复后能读到
  ↓ 返回 True
  ↓ _build_messages_opt() ← 现在会走这条路
  ↓ HistoryCompressor + MemorySummarizer 生效
  ↓ DeepSeek API（压缩后的 prompt）
```

**注意**：控制台 reload 端点对 token_opt 走默认分支（仅 sync_config），由于 `src.config.load_config()` 有缓存，切换后可能需要重启服务才能生效。这是已知限制，不属于本次修复范围。

### Token 模块真实可用性

修复后：**可用**（需手动在 config.yaml 中设置 `token_opt.enabled: true` 或设置环境变量 `YUYI_TOKEN_OPT=true`）

限制：
- 无 token 统计（输入/输出/历史/对比）
- 无动态 context 调整
- 无优先级机制
- 配置切换后需重启服务（缓存问题）

### P4 优先级调整建议

根据本次审计结果，P4 优先级调整为：

1. **Token 优化**（本次完成）：修复断链，验证可用 ✅
2. **Runtime 统一入口**：emotion 闭环、memory 实时向量更新、RuntimeContext 整合
3. **控制台完善**：让 reload 端点真正热重载 token_opt 配置（解决缓存问题）
4. **屏幕控制闭环**
5. **日志审计和权限**
6. **部署稳定性**

---

## [Phase 4 - 2026-07-29] Phase 4 审计报告

### 生成文件
- `PHASE4_AUDIT_REPORT.md` — 完整审计报告，覆盖部署架构、控制台、核心能力闭环、Remote Agent、Screen Control

### 关键发现
- 总体完成度约 60%
- 人格闭环：真实闭环
- 情绪闭环：完全断开
- 记忆闭环：部分闭环（向量索引未实时更新）
- 主动消息闭环：部分闭环（脚本模式可用）
- Screen Control：代码完整但未真正运行
