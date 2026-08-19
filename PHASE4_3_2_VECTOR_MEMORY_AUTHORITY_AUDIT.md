# Phase 4.3.2 VectorMemory Authority 审计报告

## 1. 审计目标

分析 VectorMemory 的所有创建路径、底层存储结构、Chroma/embedding/collection 生命周期，以及各消费方对 VectorMemory 的使用方式，判断 RuntimeCore 是否适合作为 VectorMemory Authority，并评估多实例分裂风险。

## 2. VectorMemory 创建路径（全量扫描）

### 2.1 生产路径（活跃代码）

| # | 文件 | 行号 | 创建方式 | 是否生产路径 | 说明 |
|---|------|------|----------|-------------|------|
| 1 | `src/orchestrator.py` | L104 | `self.vector_memory = VectorMemory()` | **是** | Orchestrator 主流程，每次创建 Orchestrator 实例时创建 |
| 2 | `src/context/context_manager.py` | L46 | `self._vector_memory = VectorMemory()` | 是 | 延迟加载，`_get_vector_memory()` 首次调用时创建 |
| 3 | `src/memory/memory_system.py` | L37 | `self.vector = VectorMemory()` | 边缘 | 被 yuyi_core.py 和 yuyi.py 使用，但两者均为旧代码路径 |
| 4 | `src/memory/memory_service.py` | L20 | `self.vector = VectorMemory()` | 边缘 | 仅当未传入 `vector_memory` 参数时创建 |

### 2.2 间接路径

| # | 文件 | 创建方式 | 说明 |
|---|------|----------|------|
| 5 | `src/core/yuyi_core.py` L41 | `self.memory = MemorySystem()` | 旧代码路径，间接创建 VectorMemory |
| 6 | `yuyi.py` L66 | `self.memory = MemorySystem()` | 旧代码路径，间接创建 VectorMemory |

### 2.3 非生产路径

| # | 文件 | 说明 |
|---|------|------|
| 7 | `src/orchestrator_hooks.py` L5 | 仅 import，未实际使用 |
| 8 | `tests/test_runtime_unification.py` | 测试代码 |
| 9 | `tests/test_selfmodel_authority.py` | 测试代码 |
| 10 | `tests/test_memory_harden.py` | 测试代码 |

### 2.4 创建路径汇总

```
生产路径（Orchestrator）: 1 个直接创建
活跃路径（ContextManager）: 1 个延迟创建
边缘路径（MemorySystem/MemoryService）: 2 个创建
旧代码路径（yuyi_core/yuyi.py）: 2 个间接创建
```

**核心结论**：当前生产环境中，**Orchestrator 和 ContextManager 各自创建了独立的 VectorMemory 实例**，这是主要的多实例分裂风险点。

## 3. VectorMemory 底层存储结构

### 3.1 存储架构

```
VectorMemory
├── chromadb.PersistentClient(path="data/chroma_db")
│   ├── Collection: "yuyi_memories"
│   │   ├── embedding_function: SentenceTransformerEmbeddingFunction
│   │   │   └── model_name: "BAAI/bge-small-zh-v1.5"
│   │   └── documents/metadata/ids (upsert)
│   └── 文件锁机制（ChromaDB 内部）
├── status_file: "data/chroma_db/index_status.json"
│   └── {version, indexed, count, updated}
└── 配置项（从 config.yaml 读取）
    ├── memory.embedding_model → "BAAI/bge-small-zh-v1.5"
    ├── memory.chroma_path → "data/chroma_db"
    ├── memory.target_user_id → "366648462"
    ├── memory.search_top_k → 8
    └── memory.min_relevance → 0.3
```

### 3.2 与 MemoryStore 的关键区别

| 维度 | MemoryStore | VectorMemory |
|------|------------|--------------|
| 底层存储 | JSON 文件 (`data/memory.json`) | ChromaDB (`data/chroma_db/`) |
| 写入方式 | 全量读写（每次 load/save 全部数据） | 增量 upsert（按 ID 幂等） |
| 多实例写入风险 | **高**（全量覆盖，后写者覆盖先写者） | **低**（upsert 幂等，ChromaDB 有文件锁） |
| 多实例资源浪费 | **低**（JSON 操作轻量） | **高**（每个实例加载 SentenceTransformer 模型 ~200MB） |
| 初始化成本 | **低**（读 JSON 文件） | **高**（加载 ChromaDB + embedding 模型） |

## 4. Chroma/embedding/collection 生命周期分析

### 4.1 初始化流程

```python
def __init__(self):
    # 1. 读取配置
    self.embedding_model_name = get("memory.embedding_model", "BAAI/bge-small-zh-v1.5")
    self.chroma_path = Path(get("memory.chroma_path", "data/chroma_db"))
    
    # 2. 创建 ChromaDB 持久化客户端
    self.chroma_client = chromadb.PersistentClient(path=str(self.chroma_path))
    
    # 3. 版本检查和可能的重建
    self._check_and_rebuild()  # 检查 index_status.json，需要时清空重建
    
    # 4. 获取或创建 collection（加载 embedding 模型）
    self.collection = self._get_or_create_collection()
    # → SentenceTransformerEmbeddingFunction(model_name="BAAI/bge-small-zh-v1.5")
    
    # 5. 记录当前索引数量
    self._indexed_count = self.collection.count()
```

### 4.2 关键资源消耗

| 资源 | 每个实例消耗 | 多实例影响 |
|------|-------------|-----------|
| ChromaDB PersistentClient | 1 个文件锁连接 | 多连接可能冲突 |
| SentenceTransformer 模型 | ~200MB GPU/CPU 内存 | **N 倍内存消耗** |
| Collection 引用 | 轻量 | 指向同一 collection，无额外消耗 |

### 4.3 Collection 操作

| 操作 | 调用方 | 并发安全性 |
|------|--------|-----------|
| `upsert` | `add_memory()`, `index_memories()` | ChromaDB 内部有锁，幂等 |
| `query` | `search()` | 只读，安全 |
| `count` | `count()`, `__init__` | 只读，安全 |
| `delete_collection` | `clear()`, `_clear_all()` | 危险操作，但仅限手动调用 |

### 4.4 Status File 生命周期

```
index_status.json:
{
    "version": "0.3.0",       # 版本号，不匹配时触发重建
    "indexed": true/false,    # 是否已完成全量索引
    "count": 42,              # 索引记忆数量
    "updated": "..."          # 最后更新时间
}
```

**风险**：多实例可能同时写入 `index_status.json`，但由于 ChromaDB 的 upsert 幂等性，实际数据丢失风险低。

## 5. 各消费方对 VectorMemory 的使用分析

### 5.1 Orchestrator（主要消费者）

| 操作 | 方法 | 读写 | 频率 |
|------|------|------|------|
| `search(user_message, top_k=5)` | `process()` Step 2 | 只读 | 每次对话 |
| `add_memory(memory_record)` | `process()` Step 10 | 写入 | 每次对话 |
| `search("最近的对话和记忆", top_k=5)` | `generate_initiative()` | 只读 | 主动消息 |

**核心发现**：Orchestrator 是 VectorMemory 的**唯一写入者**（通过 `add_memory`）。其他消费方只读。

### 5.2 ContextManager

| 操作 | 方法 | 读写 | 频率 |
|------|------|------|------|
| `search(query, top_k=limit*3)` | `get_historical_context()` | 只读 | 按需调用 |

**核心发现**：ContextManager 只读，但创建了独立的 VectorMemory 实例（包含独立的 embedding 模型加载）。

### 5.3 MemorySystem

| 操作 | 方法 | 读写 | 频率 |
|------|------|------|------|
| `search(query, top_k)` | `search()` | 只读 | 旧代码路径 |

**核心发现**：仅被 yuyi_core.py 和 yuyi.py 使用，属于旧代码路径，不在当前生产流程中。

### 5.4 MemoryService

| 操作 | 方法 | 读写 | 频率 |
|------|------|------|------|
| `search(query, top_k)` | `semantic_search()` | 只读 | 按需调用 |

**核心发现**：MemoryService 构造函数支持外部注入 `vector_memory` 参数，已有共享机制。

## 6. 多实例分裂风险评估

### 6.1 当前存在的实例

| 实例 | 创建位置 | 写入 | 资源消耗 |
|------|----------|------|----------|
| Orchestrator.vector_memory | `orchestrator.py:104` | **是** | 1x embedding 模型 |
| ContextManager._vector_memory | `context_manager.py:46` | 否 | 1x embedding 模型 |
| MemorySystem.vector | `memory_system.py:37` | 否 | 1x embedding 模型（旧路径） |

### 6.2 风险等级

| 风险类型 | 等级 | 说明 |
|----------|------|------|
| 数据覆盖/丢失 | **低** | ChromaDB upsert 幂等 + 文件锁，与 MemoryStore 的 JSON 全量覆盖不同 |
| 资源浪费 | **高** | 每个 VectorMemory 实例加载独立的 SentenceTransformer 模型（~200MB） |
| ChromaDB 锁冲突 | **中** | 多个 PersistentClient 实例并发访问同一目录，可能触发锁等待 |
| 状态文件竞争 | **低** | `index_status.json` 可能被多实例同时写入，但实际影响有限 |
| 搜索结果不一致 | **低** | 所有实例指向同一 collection，数据一致 |

### 6.3 与 Phase 4.3.1 MemoryStore 分裂风险的对比

| 维度 | MemoryStore (Phase 4.3.1) | VectorMemory (Phase 4.3.2) |
|------|--------------------------|---------------------------|
| 核心风险 | **数据覆盖**（后写者覆盖先写者） | **资源浪费**（多实例重复加载模型） |
| 风险严重性 | **高**（丢失记忆数据） | **中**（内存浪费，不影响数据正确性） |
| 写入模式 | 全量覆盖（`_save` 写整个列表） | 增量 upsert（按 ID 幂等） |
| 并发安全性 | **不安全** | **安全**（ChromaDB 内部有锁） |
| Authority 收益 | **高**（消除数据丢失风险） | **中**（减少资源浪费，降低锁冲突） |

## 7. RuntimeCore 作为 VectorMemory Authority 的可行性

### 7.1 适合的理由

1. **架构一致性**：与 Phase 4.2.x/4.3.1 的 Authority 模式一致（RuntimeCore lazy 创建 + RuntimeBridge 转发 + Orchestrator 共享 + fallback）
2. **资源优化**：共享单一 embedding 模型实例，避免多实例重复加载 ~200MB 模型
3. **减少锁冲突**：单一 PersistentClient 实例避免 ChromaDB 文件锁竞争
4. **Orchestrator 已是主要消费者**：通过 RuntimeBridge 获取共享引用是自然的

### 7.2 需要关注的风险

1. **初始化成本高**：VectorMemory 初始化需要加载 ChromaDB + embedding 模型，lazy 创建时可能有延迟
2. **ChromaDB 依赖**：外部依赖可能不可用，需要更健壮的 fallback 机制
3. **ContextManager 延迟加载模式**：需要调整为通过 RuntimeBridge 获取共享实例
4. **MemoryService 已有注入机制**：可以复用，但需确保注入的是共享实例
5. **旧代码路径**：yuyi_core.py 和 yuyi.py 通过 MemorySystem 间接创建，不在生产路径上

### 7.3 与 MemoryStore Authority 的关键差异

| 维度 | MemoryStore Authority | VectorMemory Authority |
|------|----------------------|----------------------|
| 核心收益 | 消除数据覆盖风险 | 减少资源浪费和锁冲突 |
| 修改复杂度 | 低（3 个文件） | 中（需处理 ContextManager 的延迟加载） |
| Fallback 策略 | `MemoryStore()` | 需要更复杂的降级（ChromaDB 不可用） |
| adapters_enabled 联动 | 优先返回 MemoryAdapter 的 store | RuntimeCore 无 VectorMemory 引用，需新建 |

### 7.4 RuntimeCore 当前状态

- **RuntimeCore 目前不持有任何 VectorMemory 引用**
- RuntimeCore 的 adapters（MemoryAdapter）内部使用的是 MemoryStore，不是 VectorMemory
- 需要新增 `get_vector_memory()` 方法，与 `get_memory_store()` 平行

## 8. 编码实施建议

### 8.1 修改范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/runtime/runtime_core.py` | 新增 | `get_vector_memory()` — lazy 创建，缓存唯一实例 |
| `src/runtime/runtime_bridge.py` | 新增 | `get_vector_memory()` — 转发 RuntimeCore 实例 |
| `src/orchestrator.py` | 修改 | `self.vector_memory = VectorMemory()` → 优先 RuntimeBridge 获取，fallback 自建 |
| `src/context/context_manager.py` | 修改 | `_get_vector_memory()` → 优先 RuntimeBridge 获取，fallback 自建 |
| `tests/test_memory_authority.py` | 新增 | VectorMemory Authority 相关测试用例 |

### 8.2 不修改的文件

| 文件 | 理由 |
|------|------|
| `src/memory/vector.py` | 不修改 VectorMemory 类内部 |
| `src/memory/memory_system.py` | 旧代码路径，不在生产流程 |
| `src/memory/memory_service.py` | 已有 `vector_memory` 注入机制，无需修改 |
| `src/core/yuyi_core.py` | 旧代码路径 |
| `yuyi.py` | 旧代码路径 |

### 8.3 lazy 创建策略

```python
def get_vector_memory(self) -> Optional["VectorMemory"]:
    # 1. 优先返回已创建的实例
    if hasattr(self, "_lazy_vector_memory") and self._lazy_vector_memory is not None:
        return self._lazy_vector_memory
    
    # 2. Lazy 创建（ChromaDB 可能不可用，需要 try/except）
    try:
        from src.memory.vector import VectorMemory as _VectorMemory
        self._lazy_vector_memory = _VectorMemory()
        return self._lazy_vector_memory
    except Exception as _e:
        logger.warning(f"RuntimeCore: VectorMemory lazy 创建失败: {_e}")
        return None
```

### 8.4 Fallback 策略

与 MemoryStore 不同，VectorMemory 的 fallback 需要考虑 ChromaDB 不可用的情况：

```python
# Orchestrator
self.vector_memory = None
try:
    from src.runtime.runtime_bridge import get_runtime_bridge
    _bridge = get_runtime_bridge()
    _shared_vm = _bridge.get_vector_memory()
    if _shared_vm is not None:
        self.vector_memory = _shared_vm
except Exception:
    pass

# Fallback 1: RuntimeBridge 不可用时自建
if self.vector_memory is None:
    try:
        self.vector_memory = VectorMemory()
    except Exception:
        self.vector_memory = None  # ChromaDB 不可用时降级为 None

# Orchestrator 的 vector_memory 已有 None 检查（if self.vector_memory:）
```

## 9. 审计结论

### 9.1 是否需要 VectorMemory Authority？

**需要，但优先级低于 MemoryStore Authority。**

| 维度 | 评估 |
|------|------|
| 数据安全风险 | **低**（ChromaDB upsert 幂等，无数据覆盖风险） |
| 资源浪费风险 | **高**（多实例重复加载 ~200MB embedding 模型） |
| 架构一致性收益 | **高**（与 Phase 4.2.x/4.3.1 模式统一） |
| 实施复杂度 | **中**（需处理 ContextManager 延迟加载 + ChromaDB 不可用降级） |

### 9.2 实施优先级

Phase 4.3.2 VectorMemory Authority 的核心价值是**资源优化**而非**数据安全**（与 Phase 4.3.1 MemoryStore 的数据安全驱动不同）。建议实施，但需要：

1. **更健壮的 fallback 机制**：ChromaDB/SentenceTransformer 不可用时优雅降级
2. **ContextManager 的延迟加载改造**：从自建改为通过 RuntimeBridge 获取共享实例
3. **不修改 MemorySystem/MemoryService**：旧代码路径保持不变，MemoryService 已有注入机制

### 9.3 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| ChromaDB 不可用导致 lazy 创建失败 | fallback 返回 None，Orchestrator 已有 `if self.vector_memory:` 检查 |
| embedding 模型加载耗时 | lazy 创建在首次调用时执行，不影响启动速度 |
| ContextManager 改造引入新 bug | 保留 fallback 自建逻辑，RuntimeBridge 不可用时自动降级 |
| 旧代码路径（yuyi_core.py）不受影响 | 不修改 MemorySystem，旧路径仍自建 VectorMemory |

### 9.4 审计通过条件

- [x] 全量扫描 VectorMemory 创建路径
- [x] 分析底层存储结构和 ChromaDB 生命周期
- [x] 分析多实例分裂风险（资源浪费为主，数据安全为辅）
- [x] 评估 RuntimeCore 作为 Authority 的可行性
- [x] 明确修改范围和不修改范围
- [x] 设计 fallback 策略

**审计结论：建议实施，等待用户审批后进入编码阶段。**
