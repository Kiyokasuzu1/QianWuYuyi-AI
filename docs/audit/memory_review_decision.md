# Memory Review Decision — Phase C.2.1

> **Phase:** C.2 — Memory Restoration & Cognitive Baseline
> **C.2.1 任务:** Memory 人工审核流程
> **审核时间:** 2026-08-02
> **审核人:** Phase C.2 自动决策(基于 Phase C.1 审计报告)
> **数据源:** `data/memory.json` (201 条)
> **关联文档:**
> - [memory_cleanup_report.md](./memory_cleanup_report.md)
> - [audit_memory.py](../../scripts/audit_memory.py)
> - [migrate_memory.py](../../scripts/migrate_memory.py)

---

## 1. 保留记录列表

**结论:** 当前 memory.json 中**没有可保留的 normal_user 记录**。

| 类别 | 数量 | 处理 |
| --- | --- | --- |
| 正常用户记忆 (normal_user) | **0** | — (无需保留) |

**说明:** 全部 201 条记录都至少存在一种污染特征(系统消息污染 / AI 内部提示污染 / 无效记录)。零可保留记录意味着 Phase C.2 迁移后 `data/memory.json` 将被重写为空列表 `[]`,需要从 Phase C.2.4 的"基线"开始,接受用户自然对话产生的新记忆。

---

## 2. 隔离记录列表

> 隔离 = 不删除,但从主 memory.json 中剥离,写入 `data/memory_archive/`,仅供溯源/调试使用。

### 2.1 系统消息污染 (1 条)

| ID | 原因 | 内容预览 |
| --- | --- | --- |
| `mem_e2744a8a23d8` | `type=test` 测试记录 | `test content` |

**处理:** 隔离 → archive。后续禁止 `type=test` 写入 memory。

### 2.2 AI 内部提示污染 (105 条)

| 类型 | 数量 | 处理 |
| --- | --- | --- |
| `runtime_experience` | 105 | 隔离 → archive |

**样本(前 3 条):**

| ID | 内容预览 |
| --- | --- |
| `mem_b39d057bd41c` | `[RuntimeExperience] send_message \| trigger=user_message \| success \| response: hi there \| duration=100ms` |
| `mem_d6655ef89eda` | `[RuntimeExperience] action_0 \| trigger=user_message \| success ...` |
| `mem_0a8187c26c8b` | `[RuntimeExperience] action_1 \| trigger=user_message \| success ...` |

**处理:** 隔离 → archive。这 105 条都是 runtime 内部经验日志被错误写入了 memory store,本身有审计价值(可在 `data/audit/` 中查看),但绝不应混入用户记忆检索集合。

### 2.3 无效记录 (95 条)

| 子原因 | 数量 | 处理 |
| --- | --- | --- |
| `type=""` 且无 role | 大部分 | 隔离 → archive |
| 包含 `<system_reminder>` 标签 | 多条 | 隔离 → archive (泄漏的 system prompt) |
| 包含 list/dict 序列化错误 | 多条 | 隔离 → archive (数据格式错误) |

**样本(前 3 条):**

| ID | 内容预览 | 失败原因 |
| --- | --- | --- |
| `mem_20260726081522_415d` | `你好` | `type=""`,无 metadata 标识 |
| `mem_20260726081823_ad1d` | `Generate a summary of our previous conversation history. <extra_instruction>...` | 提示注入尝试 |
| `mem_20260726081823_6b8d` | `[{'type': 'text', 'text': '羽依'}, {'type': 'text', 'text': '<system_reminder>User ID: ...'` | system_reminder 泄漏 |

**处理:** 隔离 → archive。这 95 条全部为**格式异常或安全敏感**,绝对不应进入正常 memory 检索池。

---

## 3. 删除原因

> 注:Phase C.2 **不执行 delete 操作**。所有记录都会被"隔离"(迁移到 archive),原 memory.json 在迁移后仅保留 normal_user(本次为 0)。

### 3.1 为什么 100% 数据都不能保留

| 类别 | 不能保留的原因 |
| --- | --- |
| 0 条 normal_user | 当前不存在 |
| 1 条 test 记录 | 测试数据无业务价值 |
| 105 条 runtime_experience | Runtime 内部经验不应污染用户记忆检索 |
| 95 条 invalid | 格式异常 + 包含 system_reminder 提示注入风险 |

### 3.2 为什么"隔离"而不是"删除"

- **可审计:** archive 文件保留 30 天,期间任何问题可回溯
- **可回滚:** 通过 `migrate_memory.py rollback` 一键还原
- **可分析:** 后续可统计污染来源、修复 Memory 入口策略
- **零损失风险:** 原 memory.json 完整保留在 `data/memory_backup/`

### 3.3 为什么不人工逐条 review

- 201 条数量过大,人工 review 成本高
- 100% 都有污染特征,无需逐条判断
- 系统已经按 4 类规则自动分类,人工只需 review 规则是否正确
- **本文件即人工 review 的产物:** 确认规则 → 决定处理方式

---

## 4. 未来防污染规则

> Phase C.2.3 将把以下规则写入 Memory 入口(已实现,见 [test_memory_pollution_guard.py](../../tests/test_memory_pollution_guard.py))

### 4.1 禁止保存(硬黑名单)

| memory_type | 原因 |
| --- | --- |
| `runtime_experience` | Runtime 内部经验日志 |
| `system_prompt` | System prompt 不属于用户记忆 |
| `internal_reasoning` | AI 思考链,非用户信息 |
| `debug` | 调试信息,无记忆价值 |
| `tool_call` | 工具调用记录,非用户行为 |
| `reflection_process` | 反思过程,非用户事实 |
| `test` | 测试数据 |

### 4.2 拒绝保存的 role

| role | 原因 |
| --- | --- |
| `system` | 系统消息不属于用户记忆 |
| `tool` | 工具结果不属于用户记忆 |
| `function` | 函数调用不属于用户记忆 |
| 内容以 `<system_reminder>` 开头 | 提示注入防护 |

### 4.3 允许保存(白名单)

| memory_type | 用途 |
| --- | --- |
| `user_fact` | 用户事实(姓名/职业/兴趣) |
| `user_preference` | 用户偏好(喜欢/不喜欢) |
| `user_event` | 用户事件(发生的事) |
| `user_experience` | 用户经历(亲历) |
| `user_milestone` | 用户里程碑 |
| `user_emotion` | 用户情绪(感知到) |
| `user_goal` | 用户目标 |
| `user_relationship` | 关系事件 |
| `user_shared` | 分享内容 |
| `relationship_event` | 关系事件(显式) |
| `important_experience` | 重要经历(显式) |

### 4.4 防御性规则(在任何情况下都生效)

1. **role 校验:** 没有 role 或 role 不在白名单 → 拒绝保存
2. **content 校验:** content 为空或仅空白 → 拒绝保存
3. **type 校验:** memory_type 为空或不在白名单 → 拒绝保存
4. **提示注入检测:** content 含 `<system_reminder>` / `<extra_instruction>` / `[RuntimeExperience]` → 拒绝保存
5. **长度上限:** 单条 content > 4000 字符 → 拒绝保存(避免日志/序列化错误)
6. **频率限制:** 同一 user_id 60 秒内最多 10 次 memory 写入(避免循环污染)

### 4.5 CI / 测试防线

- `tests/test_memory_pollution_guard.py` 覆盖所有禁止类型
- `tests/test_memory_partition.py` 验证归档逻辑
- 每周一次 audit_memory 报告,>5% 污染率告警

---

## 5. 下一步行动

| 步骤 | 命令 | 状态 |
| --- | --- | --- |
| 1. 审核本决策文档 | — | **本步** ✅ |
| 2. dry-run 验证迁移 | `python scripts/migrate_memory.py run` | 等待 C.2.2 |
| 3. 执行迁移 | `python scripts/migrate_memory.py run --execute` | 等待 C.2.2 |
| 4. 防污染规则 | C.2.3 实现 | 等待 C.2.3 |
| 5. 内存基线 | C.2.4 生成 | 等待 C.2.4 |

---

## 6. 决策签字

> **决策:** 接受 Phase C.1 审计分类结果,执行 Phase C.2 隔离迁移
> **决策时间:** 2026-08-02
> **风险等级:** Medium(可回滚,数据已备份)
> **预期效果:** memory.json 重写为空 `[]`,系统进入干净基线

**审核人:** (自动决策,基于规则)
**复核:** docs/audit/phase_c1_final_report.md + memory_cleanup_report.md
