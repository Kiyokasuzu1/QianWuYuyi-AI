# Perception Adapter Layer —— Phase 4.0.0 设计文档

**版本**: v1.0 (Design Only)
**日期**: 2026-07-31
**作者**: QianWuYuyi-AI Architecture Team
**状态**: 🚧 设计阶段 (无真实实现)

---

## 0. 文档目的

本文件是 **Phase 4.0.0: Perception Adapter Layer** 的架构设计文档。

本阶段**不**:
- ❌ 不实现真实的 Vision Model
- ❌ 不接入 OpenCV / PIL / pyautogui / mss / screen-capture 等库
- ❌ 不接入摄像头 / 麦克风 / 截图 API
- ❌ 不接入 LLM Vision API (如 GPT-4V / Claude Vision / Qwen-VL)
- ❌ 不修改 RuntimeContext schema
- ❌ 不修改 ResponseEngine / RealityGuard / PerceptionGuard 的事实来源体系

本阶段**只**:
- ✅ 建立 Perception Adapter 的**抽象接口**
- ✅ 建立 Screen / Vision / Audio 三类 Adapter 的**接口骨架**
- ✅ 规范 Observation → Fact 的转换契约
- ✅ 在 Runtime 生命周期中**预留** perception observation 阶段 (不实际执行)
- ✅ 通过测试守护:任何对视觉实现的依赖都被禁止

---

## 1. 设计目标

### 1.1 核心目标

为羽依 AI 引入**"感知入口"**的架构基础,让 Runtime 未来可以接入:

| 感知类型 | 未来能力 | 本阶段 |
|----------|----------|--------|
| Screen   | 截屏 / OCR / 窗口标题 | 接口骨架 |
| Vision   | 图像识别 / 物体检测 / 场景理解 | 接口骨架 |
| Audio    | 麦克风录音 / 语音转写 | 接口骨架 |
| Camera   | 摄像头画面 | 接口骨架 (纳入 Vision) |

### 1.2 设计原则

1. **依赖倒置**: Runtime 不直接依赖感知实现,而是依赖 `PerceptionAdapter` 抽象接口
2. **单向数据流**: `Adapter → Observation → Fact → RealityGuard → Response`
3. **证据链完整**: 每个 Fact 都可追溯到 Observation,Observation 记录 evidence_ids
4. **观察-事实分离**: Observation 是**原始读数**,Fact 是**带来源标记的可信陈述**;禁止跳过 Fact 直接使用 Observation
5. **不依赖具体实现**: 任何 Phase 4.0.0 之后的代码都不能 import cv2 / PIL / pyautogui / mss / OpenAI Vision 等

---

## 2. 架构总览

### 2.1 数据流图

```
                   ┌──────────────────────────────────┐
                   │   未来外部感知设备 (Phase 4.1+)  │
                   │   Screen / Camera / Mic / API    │
                   └────────────────┬─────────────────┘
                                    │ (raw data)
                                    ▼
       ┌─────────────────────────────────────────────────┐
       │           PerceptionAdapter (Abstract)            │
       │  attach() / detach() / health_check() / observe() │
       └────────────────┬────────────────────────────────┘
                        │ produces
                        ▼
       ┌─────────────────────────────────────────────────┐
       │      Observation (raw, with evidence_ids)         │
       │  observation_id / timestamp / source / content /  │
       │  confidence / evidence_ids                        │
       └────────────────┬────────────────────────────────┘
                        │ to_fact()
                        ▼
       ┌─────────────────────────────────────────────────┐
       │      Fact (with FactSource)                       │
       │  source = VISION / SYSTEM / USER_INPUT / ...      │
       └────────────────┬────────────────────────────────┘
                        │ fed into
                        ▼
       ┌─────────────────────────────────────────────────┐
       │      RealityGuard (Phase 3.9.0)                   │
       │  checks: vision/voice/scene hallucinations        │
       └────────────────┬────────────────────────────────┘
                        │
                        ▼
                Response (filtered, safe)
```

### 2.2 Runtime 与感知模块关系

```
Runtime Lifecycle
    │
    ├── START
    ├── LOAD_STATE
    ├── RECEIVE_EVENT
    ├── MEMORY_RETRIEVAL
    ├── EMOTION_UPDATE
    ├── GROWTH_EVALUATION
    ├── PERSONALITY_UPDATE
    ├── PERSONALITY_CONTEXT_BUILD
    ├── [NEW: PERCEPTION_OBSERVATION]  ← Phase 4.0.0 预留 (默认 no-op)
    ├── RESPONSE_GENERATION
    ├── GUARD_CHAIN (Reality → Perception → Personality)
    ├── RESPONSE
    ├── PERSISTENCE
    └── SHUTDOWN
```

**关键**:
- `PERCEPTION_OBSERVATION` 是**预留阶段**,不实际执行感知 (因为没有真实实现)
- Runtime 接受一个**可选的** `PerceptionAdapterRegistry` 注入
- 如果未注入,`PERCEPTION_OBSERVATION` 阶段为 no-op
- 已有调用方**无需修改任何代码**

---

## 3. Observation 数据流

### 3.1 Observation 契约 (Phase 4.0.0 增强)

```python
@dataclass
class Observation:
    # 标识
    observation_id: str       # 唯一 id (Phase 4.0.0 标准化命名)
    timestamp: str            # ISO 8601 UTC
    # 来源 / 内容
    source: FactSource        # Phase 4.0.0: source 必须是 FactSource
    content: str              # 观察内容 (OCR 文本 / 摄像头描述 / 麦克风转写)
    # 可信度
    confidence: float         # 0.0 ~ 1.0
    # 证据链
    evidence_ids: List[str]   # 支撑该 observation 的原始证据 id 列表
    # 元数据
    kind: ObservationKind     # SCREEN / CAMERA / MICROPHONE / SYSTEM / NONE
    available: bool
    meta: Dict[str, Any]
    schema_version: str = "1.0"
```

### 3.2 Observation → Fact 转换

```python
def observation_to_fact(obs: Observation) -> Fact:
    """唯一合法的转换路径。
    
    - observation.source == FactSource.VISION   → Fact(source=VISION)
    - observation.source == FactSource.SYSTEM   → Fact(source=SYSTEM)
    - observation.source == FactSource.USER_INPUT → Fact(source=USER_INPUT)
    - 其他来源不允许直接转 Fact (INFERENCE 必须由 LLM/模型生成,不由 Observation 转)
    """
    return Fact(
        content=obs.content,
        source=obs.source,        # 必须是 FactSource.VISION / SYSTEM / USER_INPUT
        confidence=obs.confidence,
        evidence_ids=[obs.observation_id],  # 关联 evidence
        meta={"observation_id": obs.observation_id, "kind": obs.kind.value},
    )
```

### 3.3 不可信观察如何处理

| 情况 | 处置 |
|------|------|
| `observation.available=False` | 跳过,不入 Fact |
| `observation.confidence < 0.5` | 转 Fact 时降权 (PerceptionGuard 标记 INFERENCE 风险) |
| `observation.kind=NONE` | 跳过,不入 Fact |
| `observation.source=INFERENCE` | **拒绝转换** (Observation 不允许来自 INFERENCE) |
| 多个 Observation 内容冲突 | 后续 Phase 处理 (Phase 4.0.0 仅记录 evidence_ids) |

---

## 4. Adapter 接口契约

### 4.1 PerceptionAdapter (Abstract Base)

```python
class PerceptionAdapter(ABC):
    """所有感知 Adapter 的抽象基类 (v1.0)。"""
    
    name: str
    observation_kind: ObservationKind
    schema_version: str = "1.0"
    
    @abstractmethod
    def attach(self) -> None: ...
    
    @abstractmethod
    def detach(self) -> None: ...
    
    @abstractmethod
    def health_check(self) -> Dict[str, Any]: ...
    
    @abstractmethod
    def observe(self) -> Optional[Observation]:
        """执行一次观察,返回 Observation 或 None (无可用数据)。"""
        ...
```

### 4.2 ScreenObservationAdapter

```python
class ScreenObservationAdapter(PerceptionAdapter):
    """屏幕感知 Adapter 骨架 (Phase 4.0.0 不实现 capture)。"""
    
    observation_kind = ObservationKind.SCREEN
    
    def capture(self) -> Optional[Observation]:
        """Phase 4.0.0: 必须 raise NotImplementedError。"""
        raise NotImplementedError("Screen capture not implemented in Phase 4.0.0")
    
    def observe_screen(self) -> Optional[Observation]:
        """Phase 4.0.0: 内部调用 capture(),后续 Phase 实现。"""
        return self.capture()
    
    def observe(self) -> Optional[Observation]:
        return self.observe_screen()
```

### 4.3 VisionAdapter

```python
class VisionAdapter(PerceptionAdapter):
    """视觉理解 Adapter 骨架 (Phase 4.0.0 不调用 LLM Vision)。"""
    
    observation_kind = ObservationKind.CAMERA
    
    def analyze(self, observation: Observation) -> List[Fact]:
        """将 Observation 转为 Fact 列表。
        
        Phase 4.0.0: 必须 raise NotImplementedError。
        后续 Phase 接入 LLM Vision / 物体检测模型。
        """
        raise NotImplementedError("Vision analyze not implemented in Phase 4.0.0")
    
    def observe(self) -> Optional[Observation]:
        # Phase 4.0.0: 不主动 capture
        return None
```

### 4.4 AudioAdapter

```python
class AudioAdapter(PerceptionAdapter):
    """音频感知 Adapter 骨架 (Phase 4.0.0 不实现 STT)。"""
    
    observation_kind = ObservationKind.MICROPHONE
    
    def listen(self) -> Optional[Observation]:
        """Phase 4.0.0: 必须 raise NotImplementedError。"""
        raise NotImplementedError("Audio listen not implemented in Phase 4.0.0")
    
    def analyze_audio(self, observation: Observation) -> List[Fact]:
        """Phase 4.0.0: 转换逻辑 (无 STT)。"""
        raise NotImplementedError("Audio analyze not implemented in Phase 4.0.0")
    
    def observe(self) -> Optional[Observation]:
        return self.listen()
```

---

## 5. Fact 来源约束

### 5.1 来源映射

| Observation.source | Fact.source | 备注 |
|--------------------|-------------|------|
| FactSource.VISION     | FactSource.VISION     | 屏幕 / 摄像头视觉描述 |
| FactSource.SYSTEM     | FactSource.SYSTEM     | 系统级观察 (窗口标题 / 进程列表) |
| FactSource.USER_INPUT | FactSource.USER_INPUT | 用户输入的转写 |
| FactSource.MEMORY     | FactSource.MEMORY     | (少见,通常 Fact 由记忆检索直接产生) |
| FactSource.INFERENCE  | ❌ **禁止转换**        | INFERENCE 必须由 LLM 生成,不由 Observation 转 |

### 5.2 验证规则

```python
ALLOWED_OBS_TO_FACT_SOURCES = {
    FactSource.VISION,
    FactSource.SYSTEM,
    FactSource.USER_INPUT,
    FactSource.MEMORY,
}

def observation_to_fact(obs: Observation) -> Fact:
    if obs.source not in ALLOWED_OBS_TO_FACT_SOURCES:
        raise ValueError(
            f"Cannot convert observation to fact with source {obs.source!r}; "
            f"allowed: {ALLOWED_OBS_TO_FACT_SOURCES}"
        )
    return Fact(...)
```

---

## 6. Screen / Vision / Audio 扩展方向 (后续 Phase)

### 6.1 Phase 4.1+ 可能实现

| Adapter | 未来实现 |
|---------|----------|
| ScreenObservationAdapter | mss / Pillow / DXcam 截屏;OCR (tesseract / PaddleOCR) |
| VisionAdapter | LLM Vision API (GPT-4V / Claude / Qwen-VL);本地模型 (YOLO / LLaVA) |
| AudioAdapter | sounddevice / pyaudio 录音;whisper / Paraformer STT |

### 6.2 架构约束

- 真实实现**必须**放在独立子包 (e.g. `src/runtime/perception/impl/`)
- Runtime 永远不直接 import `impl/` 下的代码
- 通过 AdapterRegistry 注入

---

## 7. 与 RealityGuard 的关系

### 7.1 信任链

```
ScreenObservationAdapter (将来真实实现)
  ↓ produces
Observation (source=VISION, kind=SCREEN, available=True)
  ↓ observation_to_fact()
Fact (source=VISION, content="屏幕上显示 QQ 聊天窗口")
  ↓ into
RealityGuard.check(reply, facts=[...], obs_state=...)
  ↓
  现实边界检查: 允许 reply 描述"屏幕上显示 QQ" (有 VISION 来源)
```

### 7.2 阻断场景

```
LLM 草稿: "你刚才在屏幕上看着什么?"
facts: []              (无 Observation)
obs_state: screen_available=False

RealityGuard: 阻断
  → violations: ["hallucinated_visual_observation"]
  → blocked_by: "reality"
```

### 7.3 放行场景

```
LLM 草稿: "你刚才在屏幕上看着什么?"
facts: [Fact(source=VISION, content="屏幕上显示 QQ 聊天窗口")]
obs_state: screen_available=True, visible_content="QQ 聊天窗口"

RealityGuard: 放行
  → has_visual_fact = True
  → allowed = True
```

---

## 8. 不可信观察如何处理

### 8.1 风险类别

| 风险 | 描述 | 处置 |
|------|------|------|
| 设备不可用 | screen_available=False | RealityGuard 阻断描述屏幕的 reply |
| 低 confidence | observation.confidence < 0.5 | Fact 的 confidence 同样 < 0.5,PerceptionGuard 降级 |
| 内容模糊 | observation.content="" | 跳过,不入 Fact |
| 过期 observation | observation.timestamp 早于 N 分钟 | Phase 4.0.0 不处理,后续 Phase 引入 staleness |
| 多源冲突 | screen 描述 vs camera 描述矛盾 | Phase 4.0.0 简单取最新,后续 Phase 引入 reconciliation |

### 8.2 防御性规则

```python
def observation_to_fact(obs: Observation) -> Optional[Fact]:
    """防御性转换:不可信的 Observation 返回 None。"""
    if not obs.available:
        return None
    if obs.kind == ObservationKind.NONE:
        return None
    if not obs.content or not obs.content.strip():
        return None
    if obs.source not in ALLOWED_OBS_TO_FACT_SOURCES:
        return None
    if not (0.0 <= obs.confidence <= 1.0):
        return None
    return Fact(
        content=obs.content,
        source=obs.source,
        confidence=obs.confidence,
        evidence_ids=[obs.observation_id],
    )
```

---

## 9. Runtime 集成设计

### 9.1 扩展点

`src/runtime/runtime.py` 在 Phase 4.0.0 引入:

```python
# Phase 4.0.0: 感知阶段 (预留,默认 no-op)
class RuntimeStage(str, Enum):
    ...
    PERCEPTION_OBSERVATION = "perception_observation"  # Phase 4.0.0 NEW

RUNTIME_LIFECYCLE_ORDER: List[RuntimeStage] = [
    ...,
    RuntimeStage.PERSONALITY_CONTEXT_BUILD,
    RuntimeStage.PERCEPTION_OBSERVATION,  # Phase 4.0.0 插入
    RuntimeStage.RESPONSE_GENERATION,
    ...
]
```

### 9.2 向后兼容

- **不修改** `RUNTIME_CONTEXT_SCHEMA_VERSION`
- **不修改** `RuntimeContext` 字段
- **不修改** `Event` 字段
- 新增字段全部使用 Optional / 默认值
- 已有调用方 (Phase 3.x) 无需修改任何代码

### 9.3 Event 类型扩展

`src/runtime/events.py` 在 Phase 4.0.0 引入:

```python
EVENT_TYPE_PERCEPTION_OBSERVATION = "perception_observation"
```

Event 字段不变,通过 `type=perception_observation` 区分感知事件。

---

## 10. 测试守护

### 10.1 必须通过的测试

| 测试 | 守护目标 |
|------|----------|
| 不 import cv2 | 禁止视觉库 |
| 不 import PIL | 禁止图像库 |
| 不 import pyautogui | 禁止截屏库 |
| 不 import mss | 禁止截屏库 |
| 不 import openai | 禁止 Vision API |
| 不 import sounddevice | 禁止音频库 |
| 不 import pyaudio | 禁止音频库 |
| Observation source 必须是 FactSource | 强制来源标记 |
| Observation 不能直接成为 Fact | 强制转换流程 |
| Adapter 接口存在 | 保留扩展点 |
| Runtime 不依赖 perception 实现 | 依赖倒置 |

### 10.2 后续 Phase 守护

任何 Phase 4.1+ 引入新依赖时,必须:
1. 在测试中先验证不破坏上述约束
2. 在此文档中登记新依赖
3. 在 AdapterRegistry 中显式注册新 Adapter

---

## 11. 后续阶段建议

| Phase | 主题 | 范围 |
|-------|------|------|
| Phase 4.1.x | Screen Capture 真实实现 | mss / Pillow / OCR |
| Phase 4.2.x | Vision Model 接入 | LLM Vision API / 本地模型 |
| Phase 4.3.x | Audio 接入 | sounddevice / whisper |
| Phase 4.4.x | Observation Staleness | 过期判断 |
| Phase 4.5.x | 多源冲突协调 | reconciliation 策略 |

---

## 12. 总结

Phase 4.0.0 完成了**"眼睛接口"的架构基础**:

| 成果 | 状态 |
|------|------|
| 4 个抽象接口 (PerceptionAdapter + Screen / Vision / Audio) | ✅ |
| Observation 契约增强 (observation_id / evidence_ids) | ✅ |
| Observation → Fact 转换规则 | ✅ |
| Runtime 阶段扩展点 (PERCEPTION_OBSERVATION) | ✅ |
| Event 类型扩展 (perception_observation) | ✅ |
| 测试守护 (禁止视觉实现依赖) | ✅ |
| 向后兼容 (无 schema 变更) | ✅ |
| 零真实实现 (无 cv2 / PIL / mss) | ✅ |

本阶段是**接口与契约**的奠基,**真实感知能力**留待后续 Phase。
