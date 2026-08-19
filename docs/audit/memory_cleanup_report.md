# Memory Cleanup Audit Report

> Phase: **C.1 P1-3**  
> Source: `D:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI\data\memory.json`  
> Generated at: `2026-08-02T12:33:55.954774Z`  
> Status: **WARNING**

---

## 1. 总览

| 指标 | 数值 |
| --- | --- |
| 总记忆数 | 342 |
| 正常用户记忆 | 286 (83.63%) |
| 系统消息污染 | 0 (0.00%) |
| AI 内部提示污染 | 0 (0.00%) |
| 无效记录 | 56 (16.37%) |
| **污染合计** | **56 (16.37%)** |

> 状态判定规则: 污染比例 ≥ 30% → critical; ≥ 10% → warning; < 10% → healthy

## 2. 分类明细

### 2.1 按 memory_type

| memory_type | 数量 |
| --- | --- |
| `user_shared` | 286 |
| `` | 56 |

### 2.2 按 role

| role | 数量 |
| --- | --- |
| `user` | 342 |

## 3. 污染样本(每个 category 前 5 条)

### 3.2 系统消息污染 (0 样本)

_(无)_

### 3.3 AI 内部提示污染 (0 样本)

_(无)_

### 3.4 无效记录 (5 样本)

- `mem_20260802192914_9a39` | role=`user` | type=`(empty)` | reason=`type=`
  - content: `完整链路测试消息`
  - timestamp: `2026-08-02T19:29:14.096971`
- `mem_20260802192914_93fd` | role=`user` | type=`(empty)` | reason=`type=`
  - content: `你好羽依`
  - timestamp: `2026-08-02T19:29:14.384415`
- `mem_20260802192914_a5ea` | role=`user` | type=`(empty)` | reason=`type=`
  - content: `今天测试一下集成链路`
  - timestamp: `2026-08-02T19:29:14.430534`
- `mem_20260802192914_8d2f` | role=`user` | type=`(empty)` | reason=`type=`
  - content: `第二轮`
  - timestamp: `2026-08-02T19:29:14.509375`
- `mem_20260802193609_ed01` | role=`user` | type=`(empty)` | reason=`type=`
  - content: `完整链路测试消息`
  - timestamp: `2026-08-02T19:36:09.114524`

### 3.1 正常用户记忆(对照) (5 样本)

- `mem_20260802193608_5968` | role=`user` | type=`user_shared` | reason=`type=user_shared`
  - content: `你好,今天过得怎么样?`
  - timestamp: `2026-08-02T19:36:08.763951`
- `mem_20260802193608_75c1` | role=`user` | type=`user_shared` | reason=`type=user_shared`
  - content: `这是一个测试 mock 模式的输入`
  - timestamp: `2026-08-02T19:36:08.782619`
- `mem_20260802193608_306e` | role=`user` | type=`user_shared` | reason=`type=user_shared`
  - content: `第一轮消息`
  - timestamp: `2026-08-02T19:36:08.804275`
- `mem_20260802193608_6c2f` | role=`user` | type=`user_shared` | reason=`type=user_shared`
  - content: `第二轮消息`
  - timestamp: `2026-08-02T19:36:08.823617`
- `mem_20260802193608_e8d4` | role=`user` | type=`user_shared` | reason=`type=user_shared`
  - content: `我今天完成了一个重要的项目,感觉很有成就感`
  - timestamp: `2026-08-02T19:36:08.845793`

## 4. 严重程度评估

**当前状态: WARNING**

- ⚠️ **污染比例 ≥ 10%**:memory 数据存在明显污染,需要清理。
- 建议:按计划执行迁移(见 §5)。

## 5. 安全迁移计划

**核心原则:不直接删除任何数据,所有操作分阶段执行,每阶段都可回滚。**

### Phase 1: 软隔离 (Quarantine)

- 把所有 pollution 记录的 id 写入 quarantine 列表(只读,保存在 `.cache/audit/memory_cleanup_plan.json`)。
- 不删除任何数据;运行时的 memory 检索可以通过 `quarantine_ids` 过滤掉污染记录(选择性读取)。
- 待隔离 id 总数: **56**

### Phase 2: 数据备份 (Backup)

- 在执行任何修改前,先复制 `data/memory.json` 到 `data/memory.json.YYYYMMDD_HHMMSS`。
- 备份命名规则:`memory.json.<UTC 时间戳>`。

### Phase 3: 人工 review (Manual Review)

- 由人工 review quarantine 列表(尤其是 `invalid` 类别)。
- 复核完成后,在 `docs/audit/memory_cleanup_report.md` 中签字确认。
- 建议 review 工具:`memory_audit.py --list` 命令(将提供)。

### Phase 4: 安全迁移 (Migrate)

- 把 `system_pollution` / `ai_internal_pollution` 记录从 `memory.json` 中剥离,写入 `data/memory_archive/system_ai_internal_<timestamp>.json`。
- 原 `memory.json` 仅保留 `normal_user` 记录。
- 写入前再次生成备份(覆盖 Phase 2 备份)。
- 可用 `git diff data/memory.json` 复核改动。

### 回滚策略

- 任意阶段均可通过 `cp data/memory.json.<timestamp> data/memory.json` 一键回滚。
- Phase 4 完成后,30 天内保留归档文件,之后方可清理。

## 6. 推荐的下一步

1. 运行 `python scripts/audit_memory.py --list`,查看完整 quarantine 列表(待实现)。
2. 人工 review `invalid` 类别(占比高时尤其重要)。
3. 确认无误后,运行 `python scripts/migrate_memory.py --execute`(待实现,默认 dry-run)。
4. 在 CI / 测试 pipeline 中加入本审计脚本,防止未来再次污染。

---

> 报告生成者: `scripts/audit_memory.py` (Phase C.1 P1-3)
> 数据源: `data/memory.json` (只读)
> 任何修改须经人工 review 确认,不接受自动删除操作。
