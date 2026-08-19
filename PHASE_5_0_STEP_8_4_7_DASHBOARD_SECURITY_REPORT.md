# Phase 5.0 Step 8.4.7 — Dashboard POST Security Audit 完成报告

**日期**: 2026-08-01  
**目标**: 完成 Dashboard 控制操作的安全治理链路,确保 Dashboard 控制类 POST 请求经过 Auth → Permission → Governance → Audit 完整链路,且 Dashboard 仍不是业务 Authority。

---

## 1. 新增文件

| 文件路径 | 行数 | 职责 |
|---|---|---|
| `src/admin/dashboard/_security.py` | 534 | Security Middleware(Local Only / Token / Permission) |
| `src/admin/dashboard/governance.py` | 320 | Governance Hook(risk 分类 + allowlist) |
| `src/admin/dashboard/audit.py` | 327 | Audit 封装(success / failure / denied 全覆盖) |
| `src/admin/dashboard/security_router.py` | 397 | 通用控制端点 + 审计日志只读 API |
| `tests/test_dashboard_security.py` | ~680 | ≥20 个安全治理测试,实际 38 个 |
| `static/admin/dashboard_v2/js/audit.js` | ~200 | 前端 Audit Viewer |

## 2. 修改文件

| 文件路径 | 修改内容 |
|---|---|
| `src/admin/dashboard/__init__.py` | `__all__` 导出新增的 4 个子模块 |
| `src/admin/dashboard/router.py` | 复用 `_attach_schema_meta / _jsonify / _reject_non_local` 等 helper |
| `api_server.py` | 注册 `security_v2_bp` 蓝图 |
| `static/admin/dashboard_v2/index.html` | 增加 Audit Viewer HTML 结构(只读,无控制按钮) |
| `static/admin/dashboard_v2/css/dashboard_v2.css` | 增加 Audit Viewer 样式 |
| `static/admin/dashboard_v2/js/api.js` | 增加 `auditLogs()` API 方法 |

**禁止修改范围** (已严格遵守):
- `src/memory/**`
- `src/emotion/**`
- `src/growth/**`
- `src/personality/**`
- `src/relationship/**`
- `src/runtime/**`

**Isolation 测试已通过**:
- `_security.py` 源码中不出现任何 `from src.memory / src.emotion / src.growth / src.personality / src.relationship / src.runtime / src.llm` 等 import;
- `governance.py` 同样零业务 import;
- `security_router.py` 源码中不调用 Authority / MemoryStore / PersonalityEngine / GrowthState / EmotionEngine,且不调用 openai / anthropic。

## 3. 安全治理链路(POST)

```
Frontend
  ↓
Router (security_router.py)
  ↓
Auth (_security.require_dashboard_post_auth)
  ├─ 1) Local Only  (127.0.0.1 / localhost / ::1 / 0.0.0.0)
  ├─ 2) Token 校验  (DEFAULT_DEV_TOKEN + YUYI_DASHBOARD_TOKEN 环境变量 + 注入 token)
  └─ 3) Permission  (admin=* / operator / viewer)
  ↓
Governance (governance.check_action_with_role)
  ├─ 风险分级:high / medium / low / unknown / disallowed
  ├─ 高风险需 allowlist 授权(默认仅 admin)
  └─ 默认拒绝(unknown → deny)
  ↓
Adapter (占位 dry-run,不动业务对象)
  ↓
Authority (仅占位,无业务调用)
  ↓
Audit (audit.record_dashboard_post)
  └─ 记录 who / role / action / payload_hash / timestamp / result / reason / risk / permission / remote_addr / token_hash
```

GET 链路仍保持只读:
```
Frontend → Router → Provider → Read Only Data
```

## 4. 验收标准映射

| 问题 | 答案来源 |
|---|---|
| **谁控制了羽依?** | `_security.py` 的 `Principal` + `audit.metadata.role` |
| **什么时候控制的?** | `audit.record_dashboard_post` 的 `timestamp` 字段(ISO 8601 UTC) |
| **为什么允许?** | `governance.check_action_with_role` 的 `reason` 字段 + `risk` 字段 |

## 5. 角色-权限映射(默认)

| Role | 权限 | 来源 |
|---|---|---|
| `admin` | `*`(通配,全部允许) | `DEFAULT_ROLE_PERMISSIONS` |
| `operator` | `runtime.read`, `runtime.control`, `dashboard.read`, `dashboard.control` | 同上 |
| `viewer` | `dashboard.read` | 同上 |

## 6. 高风险操作 allowlist(默认)

`runtime.disable` / `runtime.force_stop` / `authority.switch` / `authority.grant` /
`config.write` / `config.rollback` / `memory.delete` / `personality.propose` /
`growth.apply` / `emotion.override` / `audit.purge` 等仅 `admin` 可执行;
`module.reload` / `module.stop` / `audit.export` 允许 `admin` + `operator`。

## 7. 测试统计

| 测试类 | 测试数 | 覆盖点 |
|---|---|---|
| `TestAuth` | 5 | 无 token / 非法 token / 有效 token / X-Dashboard-Token / 远程拦截 |
| `TestPermission` | 5 | viewer 拒绝 / operator 允许 / admin 允许 / 通配权限 / 未知角色 |
| `TestGovernance` | 6 | 高风险审查 / 未知动作 / 低风险 / 角色授权 / 显式黑名单 |
| `TestAudit` | 8 | success / failure / denied / who / action / timestamp / sink 失败不阻断 / payload_hash 稳定 |
| `TestIsolation` | 4 | 业务 import / LLM / governance import / router Authority |
| `TestAPI` | 10 | envelope / 审计 endpoint / 过滤(who / action / result) / 高风险放行 / 高风险拒绝 / missing_action / local-only |
| **合计** | **38** | ≥ 20 要求满足(实际 38,1.9x 冗余) |

**测试结果**:
- `pytest tests/test_dashboard_security.py` → **38 passed**
- `pytest tests/test_dashboard_security.py tests/test_dashboard_provider.py ...` (Dashboard + Security 全套) → **490 passed**

## 8. 回归结果

| 测试范围 | 通过 / 总数 |
|---|---|
| `test_dashboard_security.py` (新增) | 38 / 38 |
| Dashboard V2 Providers (life_state / life_graph / life_timeline / event_stream / runtime_live2d) | 245 / 245 |
| `test_trace_envelope.py` | 31 / 31 |
| `test_admin_governance.py` | 41 / 41 |
| `test_governance_security.py` | 25 / 25 |
| 各 Dashboard Provider 测试(selfmodel / memory / goal / initiative / reflection / runtime) | 110 / 110 |
| **Dashboard + Security 套件合计** | **490 / 490 ✅** |

仅 `tests/test_admin_phase1.py::TestAdminModulesAPI::test_start_all_modules_and_stop`(initiative 模块启停)在回归中出现 **1 个失败**,但该测试与本次安全链路完全无关,是模块启停 API 的独立问题(initiative 模块 start 返回 success=False),不属于 Step 8.4.7 范畴。

## 9. API 端点

| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/api/dashboard/v2/control` | POST | Local + Token + Permission(`dashboard.control`) | 通用控制(走完整安全链路) |
| `/api/dashboard/v2/audit/logs` | GET | Local Only | 审计日志只读(支持 who / action / result 过滤) |

## 10. 关键设计决策

1. **审计写入失败不阻断业务流**:`record_dashboard_post` 内部全 try/except 保护,sink 抛异常时返回 `False` 但不抛出。
2. **payload 哈希前先脱敏**:敏感字段(password / token / secret / api_key 等)替换为 `***`,哈希值在脱敏后稳定。
3. **失败分类细化**:`high_risk_requires_review`(无 role 上下文) / `role_not_in_allowlist`(role 不在 allowlist) / `unknown_action` / `action_disallowed` / `governance_error:*`。
4. **可注入覆盖**:`_security / audit / governance` 三个模块都支持 `set_* / reset_*` 注入,便于测试与生产差异化配置。
5. **前端 Audit Viewer 默认只读**:只展示 `who / action / timestamp / result / reason`,不提供回放 / 重放 / 撤销按钮。

## 11. 实施限制遵守

- ✅ 不新增 Dashboard 页面(仅在 Debug/Admin 区加只读 Viewer)
- ✅ 不新增业务 Provider
- ✅ 不扩展 LifeGraph / Timeline / EventStream
- ✅ 不重构已有模块
- ✅ 严格只完成安全链路
- ✅ 禁止目录零修改(`src/memory/`、`src/emotion/`、`src/growth/`、`src/personality/`、`src/relationship/`、`src/runtime/`)

---

**结论**: Step 8.4.7 任务完成。Dashboard POST 控制请求已具备完整的 Auth → Permission → Governance → Audit 安全治理链路,且与业务 Authority 完全解耦。所有目标测试通过,无架构破坏。
