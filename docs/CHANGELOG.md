# Yuyi AI Changelog

> 版本规则（P2.0 起生效）：SemVer（MAJOR.MINOR.PATCH）。
> 唯一权威版本源：仓库根 VERSION.txt（单行 SemVer）；发布版本以 Git tag 为准；
> 运行时版本以启动横幅与 /health 元数据为准。

## v1.1.0（开发中）

当前开发方向（详见 docs/roadmap/v1.1_roadmap.md）：

- Version System：统一版本识别（VERSION.txt + 启动横幅 + /health 元数据）
- User Isolation：用户隔离安全升级（_unknown_sender 沙盒，最高优先级）
- Runtime Enhancement：Runtime 生命周期增强（Event Bus / Scheduler / Reflection）

## v1.0.0（2026-08-19 封板）

- v1.0 生产冻结：tag `v1.0` → ce732731（GitHub 存档，清洁版）
- 原始完整封板：20322e5（服务器侧，含全部未跟踪文件）+ 1.3G 冻结备份
- P1 封板验证三报告：失败分类 555 项定级 / 数据可靠性 4/4 演练 / 24h 运行零应用异常
- 七大系统闭环：Identity / Memory / Emotion / Relationship / Growth / SelfModel / Runtime
- 详见 docs/audit/p1_freeze_readiness_report.md、docs/audit/v1.0_freeze_report.md

---

## 封板前历史版本（原 VERSION.txt 迁移归档，内容原样保留）

QianWuYuyi-AI 浅雾羽依
========================================
Version: 8.5 (Phase 4.0.3-C)
Codename: Governance Bypass Closure
Build Date: 2026-08-15
========================================

本版本完成项：
[Phase 4.0.3-C] Governance Bypass Closure
  - RuntimeCore from_pcr() 路径接入 GovernancePolicy.evaluate()
  - accept_self_model_suggestion() 走 ApprovalQueue.approve() → Updater.apply_proposal()
  - SelfModelUpdaterAdapter.apply_suggestion() 增加安全闸：拒绝未审批 Proposal
  - 硬契约：SelfModelStore 仅接受 AUTO_APPLY 或已批准 Proposal 写入
  - 验收测试 16/16 通过，回归测试 136/136 通过

包含子版本：
- Phase 3.8.5: Growth Pipeline Dual Update Fix
- Phase 3.8.6: SelfModelUpdater Adapter 收敛
- Phase 3.8.7: Persistence 原子化
- Phase 4.0.0: SelfModel + PersonalityGrowthHistory 持久化
- Phase 4.0.3-A/B: GovernancePolicy + ApprovalQueue 契约
- Phase 4.0.3-C: RuntimeCore 旧审批链接入 Governance Contract（当前版本）

========================================
Version: 1.1.0-Stable (V1.1 Foundation Hardening Freeze)
Codename: Foundation Freeze
Build Date: 2026-08-16
========================================

本版本完成项：
[V1.1 Foundation Hardening]
  - ProposalStorage 绝对路径锚定 + 测试单例重置（F1）
  - 12 处生产持久化裸写 → atomic_write_json + 路径锁 + 损坏备份（F2）
  - 测试默认隔离收口：get_* 访问器覆盖、真实 LLM 默认跳过、会话级数据污染探针（F3/F4）
  - 测试残留数据清理（F5）
  - v1_1_foundation_smoke_test 6/6 通过；核心回归 0 新增 A 类失败
  - 详见 data/validation/v1_1_foundation_final_report.md

========================================
Version: 1.1.1 (Context Continuity Hotfix)
Codename: 上下文连续性热修复
Build Date: 2026-08-17
========================================

本版本完成项：
[V1.1.1 Context Continuity Hotfix]
  - 修复 Runtime 主链回复 Prompt 缺少最近对话历史导致的"断片/两个人在说话"
  - Phase 1: orchestrator.history → RuntimeContext.history → PromptBuilder.history
    接通（含按 user_id 隔离、legacy fallback 补录、重启自动恢复）
  - Phase 2: 身份事实常驻注入（preferred_name / forbidden_names，事实参考非指令）
  - Phase 3: 记忆检索 query 融合最近 3 轮上下文 + 剥离宿主注入块
  - 历史清洗契约：get_recent_history 为 Prompt 侧唯一出口（RAG/reminder 剥离、
    多模态归一、单条 600 字符封顶；持久化原始数据保真）
  - 新增 tests/test_v1_1_1_context_continuity.py（24 用例）：
    10 轮连续性 / 指代理解 / 称呼稳定 / 多用户隔离
  - 详见 V1_1_1_CONTEXT_CONTINUITY_HOTFIX_REPORT.md

========================================
Version: 1.1.1-p2 (Context Continuity Hotfix Patch 2)
Codename: 来源切换修复 + 持久化加固
Build Date: 2026-08-18
========================================

本版本完成项：
[V1.1.1-p2]
  - 修复升级后"上下文来源切换"：旧格式文件首启时把全局历史 seed 进
    主用户桶（第 1 轮看全局、第 2 轮起只剩桶 → 模型把真话当幻觉的
    线上案例修复）
  - _persist_history 换 atomic_write_json（原子写 + 损坏防护，崩溃
    瞬间不再可能写坏 conversation_history.json）
  - 可观测性：每轮 recent_history 注入量落 INFO 日志
