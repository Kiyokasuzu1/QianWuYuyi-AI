# Phase 4.2-A 架构影响报告：RelationshipEvent 契约统一

日期：2026-08-12
阶段：Phase 4.2-A（Relationship 契约修复，路线图 4.2-A → 4.2-B → 4.2-C → 4.2-D 的第一步）

---

## 1. 背景与根因

HEAD 提交 `eed2de6` 将 `src/relationship/relationship_event.py` 从旧 dataclass
一次性替换为 R2.5.2-B TypedDict schema，但旧 API 消费方未同步迁移：

- `relationship_event_extractor.extract()` 产出变成裸 dict（新键），
- `relationship_evaluator` 仍按旧 dataclass 读 `.event_type` / `.signal_strength` 等属性
  → `AttributeError` → 被全链路 try/except fail-soft **静默吞掉**。

结果：关系链路「提取 → 评估 → 模型 → 状态」整条断路，但对外无任何报错。
这正是羽依设计理念要避免的"数据类型靠结构字段表达"被破坏的典型案例。

## 2. 修改内容（最小修改、单向迁移、无兼容层）

**契约设计**：以新 TypedDict schema 为唯一事实来源，取其与提取产出的并集——

- FROZEN_KEYS 11 项（注册事件必备，不变）；
- ALLOWED_TYPES 增加 4 个提取类型：`collaboration` / `trust_building` /
  `boundary_respect` / `preference_learning`；
- TypedDict 增加 2 个可选键：`evidence_ids` / `potential_dimensions`；
- 新增 `RELATIONSHIP_EVENT_EXTRACTION_KEYS` 与
  `RELATIONSHIP_EVENT_REGISTERED_KEYS = FROZEN(11) + EXTRACTION(2)`。

**源码迁移（7 个文件）**：

| 文件 | 修改 |
|---|---|
| `src/relationship/relationship_event.py` | 上述 schema 并集与键集合常量 |
| `src/relationship/relationship_memory.py` | `_enforce_shape` extra 检查改用 REGISTERED（missing 仍查 FROZEN 11），gate 语义不变 |
| `src/relationship/relationship_event_extractor.py` | `extract()` 统一产出 11+2 键 dict（id=md5 稳定、UTC ISO 时间、status="observed"） |
| `src/relationship/relationship_evaluator.py` | `VALID_TYPES` 对齐 ALLOWED_TYPES；全部改 dict 键访问 |
| `src/relationship/relationship_model.py` | `process_interaction` 插入本地映射块，替换全部属性访问（event_type/timestamp/signal_strength/evidence_ids/description 共 20 余处） |
| `src/orchestrator.py` / `src/orchestrator_hooks.py` | 两处构造改统一 dict；`to_dict()` → `dict(event)`（`type="interaction"` 故意不入枚举 → evaluator 拒绝，与断裂前行为一致；该朴素加减法路径留待后续阶段退役） |

**测试迁移（3 个文件）**：extractor / evaluator / data 三个测试文件同步到新键访问；
intelligence_engine 测试无需改动。

## 3. 修改原因

- 任务卡约束：新 TypedDict schema 为唯一事实来源、不复活 dataclass、不留永久兼容层；
- 单向迁移消灭"同一事件两种形态"，让 fail-soft 吞错点消失（类型错误现在会显式暴露）；
- gate 兼容：bridge 事件 exact 11 键不变，提取事件 11+2 键可入库，来源由结构字段
  （`status` / 键集合）表达，不靠内容前缀——与 Phase 4.1C Memory Continuity 修复同一原则。

## 4. 测试结果

**运行方式**：`"$DAIMON_USER_PYTHON" -m pytest`（用户 Python 3.14.6 + pytest 9.1.1）。

| 套件 | 修复前 | 修复后 |
|---|---|---|
| 10 个 relationship 测试文件 | 16 failed / 63 passed | **80 passed / 0 failed** ✅（任务卡验收达成） |
| `tests/runtime` | — | 160 passed / 1 failed（唯一失败为既有问题，见 §6-a） |
| `tests/runtime/test_relationship_intelligence_runtime.py` | failed | **转绿**——config 本有 `relationship_enabled: True`，唯一病因即契约断裂；本次修复同时打通 record → engine → state/model → 持久化 → 事件通知整条写路径 |

**基线对照**（`git stash` → 跑 → `git stash pop`）确认以下失败均为既有，非 4.2-A 引入：

- `test_identity_context_phase2.py::...test_fallback_when_engine_missing_no_exception`（基线同败）；
- `tests/test_growth_digest_baseline_r2_5_0_b.py` 4/5 失败（HEAD 基线同样 4 failed / 1 passed）。

## 5. 测试污染问题定位（组合运行专用，已定性）

现象：`pytest tests/runtime tests/test_engine_context.py` 组合运行时，
engine_context 3 个测试 + fallback 测试报 `isinstance(<MagicMock 'mock.OpenAI()...'>, str)` 失败；
单独运行各自全绿。

**根因（三要素叠加，全部既有，与 4.2-A 无关）**：

1. `tests/test_engine_context.py:9` 模块级**无条件** `sys.modules['openai'] = MagicMock()`
   （pytest 收集期即生效，污染整个会话；其他同类文件都有 `if "openai" not in sys.modules` 守卫，唯独它没有）；
2. 仓库根 `.env` 含 `DEEPSEEK_API_KEY`，`src/config.py:8` import 时 `load_dotenv()` 注入测试进程；
3. `src/engine.py` 模块级 `from openai import OpenAI` 在 mock 注入后执行 → `OpenAI` 绑定 MagicMock，
   又因病 Key 存在 `mock_mode=False` → `client.chat...create()` 返回 MagicMock。

诊断实验证明：单独 mock 污染（无 Key）时 `mock_mode=True` 返回正常字符串；
单独 Key（真实 OpenAI 绑定）时也正常——二者叠加才产生 MagicMock 回复。
组合顺序调换可复现/消除，属**测试隔离债**，非生产 bug。
4.2-A 修改文件均不涉及 openai / engine / config 导入图。

**建议（后续单独处理，不在 4.2-A 范围）**：
`test_engine_context.py` 改用 autouse fixture + `monkeypatch.setitem` 或加守卫与还原。

## 6. 架构影响

- **关系链路复活**：extractor → evaluator → RelationshipModel → RelationshipState →
  IntelligenceEngine 全链路恢复真实数据流。IntelligenceEngine 正是设计理念要求的
  「经历 → 事件理解 → 关系状态」模型，而非好感度计数器——4.2-A 没有引入任何数值好感度模型。
- **契约单一事实来源**：RelationshipEvent 全仓库统一为 TypedDict 一种形态，
  键集合常量（FROZEN / EXTRACTION / REGISTERED）使 gate 校验可审计、可扩展。
- **运行时写路径打通**：`relationship_enabled: True` 下 Runtime 每轮交互的关系事件可真实落库，
  为 4.2-B 接入 Runtime Stage 奠定前提。
- **自我连续性收益**：关系记忆重新成为 Growth / SelfModel 的真实输入源之一，
  支撑「人格连续性 / 成长可解释性」目标。

## 7. 遗留事项

| # | 事项 | 状态 / 建议 |
|---|---|---|
| a | `test_fallback_when_engine_missing_no_exception`：ResponseAdapterImpl 的 `attach()` 在 `response_engine=None` 时经 `src.response.engine` 延迟加载成功，忽略显式 `fallback_reply="兜底回复"`，返回人格化兜底。基线同败。 | 既有语义问题，**需用户决策**：有意行为（懒加载优先）还是 bug（显式 fallback 应优先） |
| b | engine_context 组合污染（§5） | 测试隔离债，建议单独修复 `test_engine_context.py` 的模块级 mock |
| c | `test_growth_digest_baseline_r2_5_0_b.py` 4/5 失败 | HEAD 基线同败，R2.5.0-B 遗留 digest 语义漂移，建议单独立项审计 |
| d | orchestrator / orchestrator_hooks 的 `type="interaction"` 朴素加减法路径（trust±0.05） | 按路线留待 4.2 后续阶段退役 |
| e | **Phase 4.2-B**：`record_relationship_interaction` 生产零调用方；Runtime Stage 14 `relationship_context={}` 硬编码（runtime_core.py:4986） | 路线图下一步 |
| f | 服务器侧补跑全量 pytest；生产 `.env` 需配 `YUYI_ADMIN_TOKEN` / `YUYI_REMOTE_TOKEN` | 运维提醒，持续有效 |

## 8. 结论

4.2-A 验收达成：契约统一完成、80 个关系测试全绿、运行时集成测试转绿、
无新增 Relationship 系统、无兼容层、无数值好感度模型。
羽依的关系能力从"断裂且静默"恢复为"单一契约、全链路可追踪"，
为 4.2-B（接入 Runtime 每轮交互）扫清了前提。
