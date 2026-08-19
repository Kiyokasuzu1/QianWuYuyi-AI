# Phase 3.9.0 Completion Report — Reality Grounding Layer

**Project**: QianWuYuyi-AI (千语羽依)
**Phase**: 3.9.0
**Date**: 2026-07-31
**Status**: ✅ COMPLETED
**Test Result**: 32/32 Phase 3.9.0 tests passed; 455/455 Phase 3.6.x → 3.9.0 regression passed

---

## 1. 目标回顾

建立羽依的**现实感知边界 (Reality Grounding Layer)**, 解决模型虚构看到用户行为的问题。

羽依必须明确区分四类信息来源:
1. **USER_INPUT** — 用户主动告诉的信息
2. **MEMORY** — 历史保存的信息
3. **OBSERVATION** — 真实感知设备提供的信息
4. **INFERENCE** — 基于已有信息的推测

**核心禁令**: 没有 Observation 时, 不得描述用户屏幕、游戏画面、动作、表情、环境。

---

## 2. 交付物清单

### 2.1 新增文件

| 路径 | 角色 | 行数 |
|------|------|------|
| `src/runtime/perception/observation.py` | Observation dataclass (感知设备读数契约) | 142 |
| `src/runtime/perception/observation_state.py` | ObservationState dataclass (感知状态快照) | 156 |
| `src/runtime/perception/reality_guard.py` | RealityGuard (现实边界审计器) | 335 |
| `tests/test_phase_3_9_0_reality_grounding.py` | 32 个测试用例 | 495 |

### 2.2 修改文件

| 路径 | 改动 |
|------|------|
| `src/runtime/perception/__init__.py` | 导出 Phase 3.9.0 新增类 |
| `src/runtime/response_guard_chain.py` | 三 Guard 链路: Reality → Perception → Personality |

### 2.3 限制遵守

- ✅ **未实现** Vision Model
- ✅ **未实现** Screen Capture
- ✅ **未修改** RuntimeContext schema (perception_state 通过内部属性挂载)
- ✅ **未修改** 已有 API 字段签名 (向后兼容)
- ✅ **未修改** `config.yaml` 中任何内容

---

## 3. 核心数据契约

### 3.1 Observation (v1.0)

```python
@dataclass
class Observation:
    kind: ObservationKind          # NONE / SCREEN / CAMERA / MICROPHONE / SYSTEM
    content: str                   # OCR 文本 / 摄像头描述 / 麦克风转写
    available: bool                # 设备可用且已读到数据
    confidence: float              # 0.0 ~ 1.0
    source: str                    # "screen_capture" / "vts_camera" / ...
    timestamp: str                 # ISO 8601 UTC
    id: str                        # 唯一 id
    schema_version: str = "1.0"
    meta: Dict[str, Any]
```

**关键不变量**: `is_real` = `available=True and kind != NONE`。

### 3.2 ObservationState (v1.0)

```python
@dataclass
class ObservationState:
    screen_available: bool = False
    camera_available: bool = False
    microphone_available: bool = False
    visible_content: Optional[str] = None   # 屏幕/摄像头最近内容
    current_scene: Optional[str] = None     # 当前场景
    timestamp: str
    source: str
    observations: List[Observation]
    schema_version: str = "1.0"
```

**派生方式**: `ObservationState.from_observations([obs1, obs2, ...])` 自动从 observation 列表聚合可用设备。

**辅助方法**:
- `has_screen() / has_camera() / has_microphone()`
- `can_describe_visual() / can_describe_audio() / has_any_observation()`
- `to_dict() / from_dict()` 序列化

### 3.3 RuntimeContext 集成 (向后兼容)

```python
# 不修改 RuntimeContext schema
ctx = RuntimeContext(user_input="hi")
state = ObservationState(screen_available=True)
setattr(ctx, "_perception_state", state)   # 内部属性挂载
getattr(ctx, "_perception_state") == state  # True
```

**关键**: 不修改 `RuntimeContext` 的 dataclass 字段, 不影响 Phase 3.6.x / 3.7.x / 3.8.x 任何已存在调用方。

---

## 4. RealityGuard 核心逻辑

### 4.1 审计类别

| 类别 | 触发模式 (示例) | 阻断条件 |
|------|----------------|----------|
| 视觉 (visual) | "你刚才在屏幕上看什么?" "你刚才游戏里操作很厉害啊" "你看起来很开心,表情很放松" | `not (screen_available or camera_available or VISION_Fact or USER_INPUT_Fact or MEMORY_Fact)` |
| 音频 (audio) | "我听到你在说话" "你刚才说..." | `not (microphone_available or VISION_Fact or USER_INPUT_Fact or MEMORY_Fact)` |
| 场景 (scene) | "你周围环境很安静" "你家里很暗" | `not (current_scene or VISION_Fact or USER_INPUT_Fact or MEMORY_Fact)` |

### 4.2 关键设计原则

| 来源类型 | 能否放行视觉/音频/场景描述 |
|----------|----------------------------|
| USER_INPUT Fact (用户说过) | ✅ 允许 |
| MEMORY Fact (历史记忆) | ✅ 允许 |
| VISION Fact (视觉描述 fact) | ✅ 允许 |
| INFERENCE Fact (模型推测) | ❌ **禁止** (不构成 grounded 依据) |
| 无任何依据 | ❌ **禁止** |

### 4.3 报告与处置

```python
@dataclass
class RealityGuardReport:
    allowed: bool = True
    violations: List[str] = field(default_factory=list)
    needs_modify: bool = False
    needs_refusal: bool = False
    suggested_text: Optional[str] = None
    notes: List[str] = field(default_factory=list)
```

**处置策略**: 任何感知边界违规都是严重的 (模型在虚构用户行为) → 直接 `needs_refusal=True`, 建议文本为 "我没有看到这些。"

### 4.4 wrap_reply 行为

| 情况 | 输出 |
|------|------|
| 无违规 | 原 reply |
| 有违规 | hedge 前缀 + 原 reply (如 "（我没有真实看到你说的情况,所以无法判断）xxx") 或 refusal text |

---

## 5. ResponseGuardChain 三 Guard 链路

### 5.1 顺序 (Phase 3.9.0)

```
LLM draft reply
     ↓
RealityGuard.check(reply, facts, obs_state)    # 现实边界
     ↓
PerceptionGuard.check(facts)                  # INFERENCE 降级 (Phase 3.8.x)
     ↓
PersonalityGuard.check(reply, prc)            # 人格一致性 (Phase 3.8.x)
     ↓
final_reply
```

### 5.2 阻断优先级

- `blocked_by = "reality"` — 现实边界违规, **最先** 触发
- `blocked_by = "perception"` — INFERENCE 比例过高
- `blocked_by = "personality"` — 人格不一致
- `blocked_by = None` — 全部通过, final_reply = original

### 5.3 向后兼容

- `run(reply, facts, prc)` 仍接受原有参数
- `obs_state` 是可选 kwargs (Phase 3.9.0 新增)
- `reality_guard` 是可选构造函数参数 (默认 `RealityGuard()`)
- 既有调用方 (Phase 3.8.x) 无需修改任何代码

---

## 6. 测试覆盖 (32 用例 / 12 测试类)

### 6.1 测试类一览

| 编号 | 测试类 | 用例数 | 覆盖目标 |
|------|--------|--------|----------|
| T1 | TestNoScreenObservation | 4 | 无视觉输入禁止描述屏幕/游戏/表情/场景 |
| T2 | TestUserInputAllowed | 1 | USER_INPUT Fact 允许引用 |
| T3 | TestMemoryAllowed | 1 | MEMORY Fact 允许引用 |
| T4 | TestObservationAllowed | 3 | Observation 信息允许引用 |
| T5 | TestInferenceNotObservation | 1 | INFERENCE 不伪装 Observation |
| T6 | TestRuntimeNoVisionDependency | 3 | Runtime 不依赖 Vision 实现 |
| T7 | TestGuardChainOrder | 5 | ResponseGuardChain 顺序: Reality → Perception → Personality |
| T8 | TestObservationStateDerivation | 4 | ObservationState 派生与序列化 |
| T9 | TestObservationFields | 3 | Observation / ObservationState 字段完整性 |
| T10 | TestRuntimeContextPerceptionState | 3 | RuntimeContext schema 不变 + 内部挂载 |
| T11 | TestRealityGuardWrap | 3 | wrap_reply 行为 (hedge / 原样 / 通过) |
| T12 | test_phase_3_9_0_summary | 1 | 阶段总结 |

### 6.2 关键测试用例

#### 视觉违规检测
```python
def test_describe_screen_without_observation_blocked():
    rg = RealityGuard()
    report = rg.check("你刚才在屏幕上看什么?")
    assert not report.allowed
    assert "hallucinated_visual_observation" in report.violations
```

#### INFERENCE 不放行
```python
def test_inference_fact_does_not_satisfy_visual_requirement():
    rg = RealityGuard()
    report = rg.check(
        "你刚才在屏幕上看什么?",
        facts=[_inference_fact("用户可能在看屏幕")],
    )
    assert not report.allowed
```

#### 三 Guard 链路顺序
```python
def test_reality_blocks_hallucination_first():
    chain = ResponseGuardChain()
    result = chain.run(
        reply="你刚才屏幕上的内容很有趣(我猜的)",
        facts=[Fact(content="用户可能看了X", source=INFERENCE)],
        obs_state=None,
    )
    assert result.blocked_by == "reality"   # 现实边界最先生效
```

#### Runtime 不依赖 Vision
```python
def test_perception_module_does_not_import_vision():
    for py_file in perception_path.glob("*.py"):
        text = py_file.read_text()
        assert "import cv2" not in text
        assert "import PIL" not in text
        assert "import pyautogui" not in text
        assert "import mss" not in text
        assert "from src.vision" not in text
        assert "from src.screen_capture" not in text
```

---

## 7. 回归测试结果

### 7.1 Phase 3.6.x → 3.9.0 完整回归

```
tests/test_phase_3_6_3_integration.py
tests/test_phase_3_6_4_schema_governance.py
tests/test_phase_3_6_5_schema_freeze.py
tests/test_phase_3_7_0_runtime_design.py
tests/test_phase_3_7_1_adapter_design.py
tests/test_phase_3_7_2_adapter_impl.py
tests/test_phase_3_7_3_runtime_assembly.py
tests/test_phase_3_7_4_runtime_e2e.py
tests/test_phase_3_8_0_personality_runtime.py
tests/test_phase_3_8_4_response_integration.py
tests/test_phase_3_8_5_orchestrator_runtime.py
tests/test_phase_3_9_0_reality_grounding.py

===================== 455 passed, 590 warnings in 20.26s ======================
```

### 7.2 Runtime 相关测试

```
tests/test_runtime_perception.py
tests/test_runtime_unification.py
tests/test_runtime_self_model_bootstrap.py
tests/test_runtime_production_integration.py
tests/test_runtime_lifecycle_e2e.py
tests/test_runtime_integration.py
tests/test_admin_runtime_integration.py
tests/test_agreement_runtime.py
tests/test_relationship_runtime_integration.py
tests/test_emotion_runtime_integration.py

===================== 212 passed, 268 warnings in 10.56s ======================
```

### 7.3 已知不相关失败

`test_personality_growth_runtime.py::TestFullPersonalityGrowthLifeCycle::test_01_end_to_end_lifecycle`

- 失败原因: `path_validation_failed: 非法 Personality path: runtime.adjustment_0/1（不在白名单中）`
- 关联模块: Personality Growth (Phase 4.x)
- 与 Phase 3.9.0 无关: **pre-existing** 失败, 不影响 Reality Grounding Layer
- 建议后续: Phase 4.x 单独修复 personality path 白名单

---

## 8. 关键设计决策

### 8.1 为什么"任何视觉违规都触发 refusal"而不是 hedge?

考虑: 视觉/场景幻觉是**严重**问题, 继续回复会强化用户对羽依的误解。
- hedge (例如 "（我没有真实看到你说的情况）你刚才...") 仍保留了"你刚才..."的断言, 用户仍可能误以为羽依真的"没看到" (即承认羽依能看)
- refusal ("我没有看到这些。") 更彻底, 表达"羽依目前不具备该感知能力"

### 8.2 INFERENCE 不允许引用观察

设计原则: INFERENCE = 推测, 不是"看到"。允许 INFERENCE 引用视觉内容会**模糊感知边界**, 削弱对 INFERENCE 的约束。
- 例外: USER_INPUT / MEMORY / VISION Fact 都能放行 (因为它们是"已经记录的信息", 不是"推测")

### 8.3 不修改 RuntimeContext schema

原则: 向后兼容 + 内部扩展。
- `setattr(ctx, "_perception_state", state)` 即可挂载
- 不影响 Phase 3.6.x / 3.7.x / 3.8.x 任何已有调用方
- 后续 Phase 可选择性升级为正式字段, 不影响当下

### 8.4 模式匹配是启发式的

RealityGuard 的视觉/音频/场景模式是**正则表达式**, 可能有误报/漏报。
- 后续可通过 `visual_patterns_zh=...` 自定义扩展
- 关键 fallback: PerceptionGuard / PersonalityGuard 仍会兜底

---

## 9. 文件清单与角色

```
src/runtime/perception/
    __init__.py                # 公共 API 入口
    fact_source.py             # (Phase 3.8.x) USER_INPUT/MEMORY/VISION/SYSTEM/INFERENCE
    fact.py                    # (Phase 3.8.x) Fact 数据契约
    perception_guard.py        # (Phase 3.8.x) PerceptionGuard (信息真实性)
    observation.py             # (Phase 3.9.0) Observation 设备读数
    observation_state.py       # (Phase 3.9.0) ObservationState 感知快照
    reality_guard.py           # (Phase 3.9.0) RealityGuard 现实边界

src/runtime/
    response_guard_chain.py    # (Phase 3.9.0) Reality → Perception → Personality
    context.py                 # (未修改, 通过内部属性挂载 perception_state)

tests/
    test_phase_3_9_0_reality_grounding.py   # (Phase 3.9.0) 32 个测试
```

---

## 10. 完成度评估

| 项目 | 状态 |
|------|------|
| Observation dataclass | ✅ |
| ObservationState dataclass | ✅ |
| RuntimeContext 内部挂载 (向后兼容) | ✅ |
| RealityGuard 模式匹配 | ✅ |
| RealityGuard 12 个视觉模式 + 4 个音频模式 + 5 个场景模式 (zh+en) | ✅ |
| ResponseGuardChain 三 Guard 链路 | ✅ |
| RealityGuard 优先阻断 (blocked_by="reality") | ✅ |
| 32 个测试用例 | ✅ |
| Phase 3.6.x → 3.9.0 完整回归 (455 tests) | ✅ |
| Runtime 关联测试 (212 tests) | ✅ |
| 向后兼容 (不修改 schema / API) | ✅ |
| 不实现 Vision / Screen Capture | ✅ |

---

## 11. 后续 Phase 建议

- **Phase 3.10.x (可选)**: 引入轻量"hedge vs refusal"开关, 由配置决定违规处置强度
- **Phase 3.11.x (可选)**: 抽象 `RealityProvider` 接口, 让 Screen Capture / Camera / Microphone 子系统统一注入 Observation
- **Phase 3.12.x (可选)**: 引入"observation staleness"判断 — observation 时间过久则视为"无 observation"
- **Phase 4.x**: 修复 personality growth path 白名单 (`runtime.adjustment_0/1` 不在白名单)

---

## 12. 验收清单

- [x] `src/runtime/perception/observation.py` 创建
- [x] `src/runtime/perception/observation_state.py` 创建
- [x] `src/runtime/perception/reality_guard.py` 创建
- [x] `tests/test_phase_3_9_0_reality_grounding.py` 创建 (32 用例)
- [x] ObservationState 字段 (screen_available / camera_available / microphone_available / visible_content / current_scene / timestamp / source) 完整
- [x] RuntimeContext schema 未修改
- [x] RealityGuard.check / wrap_reply / has_violation 可用
- [x] ResponseGuardChain 顺序: Reality → Perception → Personality
- [x] `blocked_by = "reality"` 优先于 perception / personality
- [x] 所有 32 个新测试通过
- [x] Phase 3.6.x → 3.9.0 完整 455 个回归测试通过
- [x] Runtime 关联 212 个测试通过
- [x] **未实现** Vision Model / Screen Capture
- [x] 代码风格符合 PEP 8

---

## 13. 总结

Phase 3.9.0 **Reality Grounding Layer** 完整落地, 在不修改任何已有 schema / API 的前提下, 成功建立羽依的"现实感知边界":

1. **数据层**: Observation + ObservationState 双层数据契约, 描述"羽依当前能感知到什么"
2. **规则层**: RealityGuard 模式匹配 (zh+en 共 21+ 模式), 严格区分 USER_INPUT / MEMORY / VISION / INFERENCE
3. **链路层**: ResponseGuardChain 三 Guard 串联, RealityGuard 优先级最高, 任何视觉/音频/场景幻觉**直接阻断**
4. **测试层**: 32 个新测试 + 455 个全量回归 + 212 个 runtime 关联测试, 全部通过

本阶段**仅建立边界, 不实现感知**。后续 Phase 可按需接入 Screen Capture / Camera / Microphone, 提供真实 Observation, 让羽依"能且仅能"描述它真正看到的东西。
