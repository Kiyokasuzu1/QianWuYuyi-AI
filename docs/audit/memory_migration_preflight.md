# Memory Migration Preflight Report

> Phase: **C.2.0 (Preflight)**  
> Generated at: `2026-08-02T12:44:20.753507Z`  
> Status: **PASS**

## 1. 摘要

**preflight PASSED: 全部检查通过**

- 通过: **6 / 6**
- 整体状态: **✅ PASS**

## 2. 当前 memory.json 状态

| 指标 | 值 |
| --- | --- |
| 存在 | True |
| 大小(字节) | 160 |
| 大小(可读) | 160.00 B |
| 记录数量 | 2 |
| SHA-256 | `33eb4326637db3abac5f27d779f4dda1c50a43ddb7000a5fdb55d417620e7673` |
| 最后修改 | `2026-08-02T12:44:20.750158+00:00` |

## 3. 迁移计划值

| 指标 | 实际 | 预期 | 状态 |
| --- | --- | --- | --- |
| total | 2 | 2 | ✅ |
| archive | 1 | 1 | ✅ |
| keep | 1 | 1 | ✅ |

## 4. 预计迁移数量

| 类别 | 数量 |
| --- | --- |
| system_pollution | 0 |
| ai_internal_pollution | 0 |
| invalid | 1 |
| **archive 合计** | **1** |
| normal_user(保留) | 1 |

## 5. 路径信息

| 项目 | 路径 |
| --- | --- |
| backup 根目录 | `C:\Users\29553\AppData\Local\Temp\pytest-of-29553\pytest-259\test_run_execute_succeeds_with0\memory_backup` |
| archive 目录 | `C:\Users\29553\AppData\Local\Temp\pytest-of-29553\pytest-259\test_run_execute_succeeds_with0\memory_archive` |
| **rollback 路径** | `(尚无备份)` |

## 6. 检查项明细

### 6.1 backup_dir_conflict ✅

- **状态:** PASS
- **消息:** backup 目录不存在(首次执行,可安全创建): C:\Users\29553\AppData\Local\Temp\pytest-of-29553\pytest-259\test_run_execute_succeeds_with0\memory_backup
- **conflict:** `False`
- **suggested_subdir:** `None`

### 6.2 archive_dir ✅

- **状态:** PASS
- **消息:** archive 目录不存在,已自动创建: C:\Users\29553\AppData\Local\Temp\pytest-of-29553\pytest-259\test_run_execute_succeeds_with0\memory_archive

### 6.3 plan_load ✅

- **状态:** PASS
- **消息:** plan 加载成功: C:\Users\29553\AppData\Local\Temp\pytest-of-29553\pytest-259\test_run_execute_succeeds_with0\plan.json (generated_at=2026-08-02T10:00:00Z)

### 6.4 plan_values ✅

- **状态:** PASS
- **消息:** plan 值匹配预期 (total=2, archive=1, keep=1)
- **actual:**
  ```json
  {
    "total": 2,
    "archive": 1,
    "keep": 1,
    "normal_user": 1,
    "system_pollution": 0,
    "ai_internal_pollution": 0,
    "invalid": 1
  }
  ```
- **expected:**
  ```json
  {
    "total": 2,
    "archive": 1,
    "keep": 1
  }
  ```

### 6.5 memory_state ✅

- **状态:** PASS
- **消息:** memory.json 存在=True, size=160.00 B, records=2
- **state:**
  ```json
  {
    "exists": true,
    "size_bytes": 160,
    "size_human": "160.00 B",
    "sha256": "33eb4326637db3abac5f27d779f4dda1c50a43ddb7000a5fdb55d417620e7673",
    "record_count": 2,
    "last_modified": "2026-08-02T12:44:20.750158+00:00"
  }
  ```

### 6.6 rollback_path ✅

- **状态:** PASS
- **消息:** rollback 路径: (尚无备份)
- **rollback_path:** `None`

## 7. 结论

✅ **preflight 全部通过,可以执行迁移。**

执行命令:
```bash
python scripts/migrate_memory.py run --execute
```

---

> 报告生成者: `scripts/preflight_memory.py` (Phase C.2.0)
> 数据源: `data/memory.json` (只读) + `.cache/audit/memory_cleanup_plan.json` (只读)
