# QianWuYuyi-AI v1.1.0 Stable Release — Git 同步报告

> Release Engineer: 自动执行
> 日期: 2026-08-16（本地提交时间 2026-08-17 21:23 +0800）
> 任务: V1.1.0 Stable Release Freeze + Git 同步

---

## 版本 / Commit / Tag

| 项 | 值 |
|----|----|
| 版本 | v1.1.0-stable（V1.1 Foundation Hardening Freeze） |
| 本地分支 | `phase-4.0.1-selfmodel-authority` |
| Release commit | `726fa72cd873711cbd3919a89853a0659241d433`（"release: QianWuYuyi-AI v1.1.0 Stable Freeze"） |
| Tag | `v1.1.0-stable`（annotated，指向 726fa72） |
| 远程仓库 | https://github.com/Kiyokasuzu1/QianWuYuyi-AI.git |

**本地冻结状态：完成。** commit 与 tag 均已创建且验证通过（V1.1 新增测试 20/20、foundation smoke 6/6、Authority 闭包、核心回归两轮 1485P/14F（全部 B 类）/4S、真实数据哈希零污染）。

## 同步文件（本次 commit 内容）

| 类别 | 数量 |
|------|------|
| 修改文件（src/api_server/static/deploy/local_agent/配置模板） | 102 |
| 删除文件（deploy/systemd/yuyi-sender.service、initiative_sender.py） | 2 |
| 新增源文件（src/behavior、src/communication、src/contracts、atomic_write.py、experience_journal.py 等） | 43 |
| 新增测试（tests/foundation、tests/runtime、V1.0/V1.1 回归等） | 66 |
| 新增文档（AGENTS.md、CLAUDE.md、VERSION.txt、docs/*.md、phase4.0.1-baseline.md） | 24 |
| 新增 release 记录（data/validation/*.md，强制加入共 47 个） | 47 |
| **合计** | **284 files, +62475 / −2321 行** |

VERSION.txt 已追加 v1.1.0-Stable 冻结标记块（未重写历史版本记录）。

## 排除文件（未提交，保持工作区）

| 类别 | 文件 |
|------|------|
| 私有配置 | `config.yaml`（.gitignore 已声明 Private config） |
| 运行日志 | `.cache/audit/selfmodel_runtime_log.jsonl` |
| IDE/AI 工具缓存 | `.trae/` |
| 压缩包/部署包 | `Phase4.0.4-Pre_*.zip`×2、`step02a-deploy*.zip`×3、`p7211_*.zip`×3、`yuyi-full-update.rar`、`QianWuYuyi-Al_8.5备份.zip` |
| 测试运行产物 | `pytest_baseline_phase4*.log`×2、`test_b11.jsonl`、`tests/p4_1_4_20r_results.json`、`docs/audit/phase4_4a_lifecycle_snapshots.json` |
| 补丁/清单 | `phase4.0.1-baseline.patch`、`filelist.txt` |

暂存区审查：无密钥（sk-/AKIA/ghp_/私钥模式扫描为空）、无 .env 实际值、无数据文件（data/ 下仅 47 个 .md 报告）、无二进制缓存。

## 仓库状态：本地冻结 ✅ / 远程同步 ❌（阻塞，需人工决策）

### 现象
1. `git push origin phase-4.0.1-selfmodel-authority` → `curl 55 Send failure: Connection was reset`（多次重试）。
2. 诊断探针推送（同远端已有 commit 的引用）在重试后成功、随后可正常删除 → 网络间歇性可用，账号凭证有效。

### 根因（决定性）
分支历史中 commit `ad20c6e`（"snapshot: pre-Phase4 baseline"，本任务之前已存在）加入了 **VTube Studio 应用目录共 4.66 GiB / 1550 文件**，其中 **10 个文件超过 100 MiB**（GitHub 单文件硬限制），20 个文件超过 50 MiB。远程 4 个分支均不含这些对象（本地领先 3 个 commit，无冲突），因此任何对该分支的推送都必须上传这 ~5 GiB 负载，且会被 GitHub 服务端以超大文件拒绝——即使网络完全通畅也无法成功。

### 为什么没有自动处理
任务约束明确禁止：清理历史数据、删除未知文件、直接 reset、重构。将 VTube 二进制从历史中剥离（filter-repo）或迁移 Git LFS 都属于**破坏性历史改写**，需要人工决策。

### 可选修复路径（需人工选择，按推荐顺序）
1. **Git LFS 迁移**（推荐，保留全部历史）：`git lfs migrate import` 处理 `VTube Studio/**`，改写历史后强制推送（需临时解除分支保护 + `--force-with-lease`）。远程仓库空间占用可接受。
2. **历史剥离**：`git filter-repo --path "VTube Studio" --invert-paths`，彻底移除二进制历史，仓库瘦身为纯源码仓库（VTube 资产另行保存于本地/网盘）。会改写全部 commit hash。
3. **维持本地冻结**：v1.1.0-stable 已在本地 tag，可作为部署基线；GitHub 同步推迟到决策后。

以上任一方案执行前建议先对 `.git` 做完整备份（当前 2.9 GiB pack，含工作历史）。

## 部署建议

- 部署基线 = 本地 tag `v1.1.0-stable`（commit 726fa72），直接从此检出部署即可，不依赖 GitHub 同步。
- 部署前置检查：V1.1 foundation smoke `tests/foundation/v1_1_foundation_smoke_test.py` 6/6、V1.1 新增测试 20/20、核心回归两轮 1485P/14F（全部 B 类）/4S、V1.0 smoke 与 Authority 闭包测试通过、真实 API 测试默认跳过；真实数据文件哈希核验零污染。
- 建议 GitHub 同步决策落地后再在远程打同名 tag（当前 tag 仅存在于本地）。

## 约束遵守声明

- IDENTITY_CORE：未修改 ✅
- 人格成长逻辑：未修改 ✅
- 新增功能/模块：无 ✅
- 架构重构：无 ✅
- 历史数据清理：未执行（VTube 问题仅记录，未触碰）✅
- 未知文件删除：未执行（仅提交了分类明确的源码/文档/测试；工作区排除项原样保留）✅
- 未使用 reset / force push ✅
