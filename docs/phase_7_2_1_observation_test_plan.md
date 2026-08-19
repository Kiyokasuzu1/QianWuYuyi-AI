# Phase 7.2.1 观察期手动测试流程

> 基线 commit: `eed2de6`
> 目标: 30 分钟内手动完成全部 4 项观察验收，不需要写测试代码
> 工具: 浏览器（Dashboard） + 2 个聊天窗口（或手动脚本） + 终端 `time` 统计

---

## 0. 前置准备（5 分钟）

### 0.1 确认当前在基线 commit
```
git log --oneline -1
# 期望: eed2de6 Phase 7.2.1: Cognitive Trace - Memory/Personality/Emotion hooks + path_decided
```
不在 → 切换到 `eed2de6` 或等观察期间结束再切。

### 0.2 启动服务
```
# 终端 A
python api_server.py
```
等 20 秒确认：
- 控制台没有红色 ERROR（只有 INFO/WARN 正常）
- 打开 `http://127.0.0.1:5000/admin/dashboard` 能登录

### 0.3 打开 Dashboard Runtime Center 观察卡片
- 浏览器打开 Runtime Center → 事件流卡片（新的 Timeline）
- 打开 DevTools → Network 面板，过滤 `/api/admin/runtime/events/stream`，能看到 SSE 持续推送 heartbeat 行
  （或轮询时 `/recent` 正常返回 JSON）

---

## ① Trace 连续性测试（10 分钟）

### 场景 1: 连续消息 50 条 — 最快 2 分钟跑完

#### 方式 A: 脚本辅助（推荐）
```bash
cd d:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI
python scripts/phase_7_2_1_observe_runner.py --trace-continuity --messages 50
```
脚本跑完会自动打印:
```
[Trace 连续消息 50 条 结果]
  session_id 一致: PASS / FAIL  （应为 PASS）
  trace_id 每条不重复: PASS / FAIL  （应为 PASS）
  无跨轮 trace_id 混入: PASS / FAIL  （应为 PASS）
  结果文件: data/phase721_observation/trace_continuous.txt
```

#### 方式 B: 手动聊天
- 打开 **1 个聊天窗口**（A 窗口）
- 按顺序发送 20 条不同消息，例如:
  ```
  1 你好
  2 今天天气怎么样
  3 我们之前聊了什么
  ...（20 条就行，不够可以继续）
  ```
- 记录 Dashboard 事件流里 `trace_id` / `session_id`:
  - 每条消息应该有 **独立的 trace_id**（前缀 `lc_` 每次不同）
  - session_id 应该 **20 条都一样**
  - 检查关键: 第 19 轮的 `memory_ids` 是否出现在第 20 轮 `cognitive.memory.retrieved` 里
    → 如果有，说明 thread-local 没清，记为"失败: trace 串线"

### 场景 2: 多窗口并发聊天 — 3 分钟

**步骤**:
1. 打开 **2 个不同浏览器窗口**（不是同一浏览器的两个 tab，要用 Chrome + Edge / Chrome 两个 Profile + 正常窗口），**都开 Dashboard + 都进入 Runtime Center Timeline 观察页**
2. 两个窗口 **同时**发送消息（每条 10 条），最好速度差不多
3. 观察两个窗口的 Timeline:
   - A 窗口发出的 `cognitive.memory.retrieved` 里 `query_hash` 应该对得上 A 窗口自己说的话
   - B 窗口同理
   - **关键**: A 窗口绝对不能出现"对 B 窗口消息的 memory search 结果"
     → 如果有，记为"失败: 多窗口并发串线"

或者用脚本:
```bash
python scripts/phase_7_2_1_observe_runner.py --trace-concurrent --messages 20
# 期望: no cross-session leakage
```

### 场景 3: Runtime 重启 — 3 分钟

**步骤**:
1. 先在聊天窗口发 3~5 条消息，**记录最后一条的 trace_id = `lc_old_last`**
2. `Ctrl+C` 杀掉 api_server.py
3. 重新启动: `python api_server.py`
4. 等服务就绪（20 秒后 Dashboard 连得上）
5. 发 **1 条新消息**，看 Dashboard Timeline 的第一条事件 trace_id:
   - **绝对不能等于 `lc_old_last` 或之前任何一个 trace_id**
   - 如果等于: "失败: Runtime 重启后 thread-local 泄漏（通常是 global 变量没清）"

---

## ② Timeline 信息密度（5 分钟）

### 数据采集
方式 A（推荐）:
```bash
python scripts/phase_7_2_1_observe_runner.py --timeline-density --messages 10
```
脚本跑完会输出:
```
[Timeline 密度分析 (10 轮)]
  每轮 cognitive event 数: 平均 X.XX，最大 Y
  重复 memory.retrieved 次数: Z
  评价: 好 / 可接受 / 过载
```

方式 B（手动）:
1. 发 10 条不同长度的聊天消息（10 字 / 30 字 / 50 字混合）
2. 对每轮数一下 Timeline 里 **颜色条有紫色/橙色/粉色/灰蓝**（cognitive.*）的数量
   → 不要把 runtime.* 的蓝条算进去
3. 记录:
   ```
   第1轮: ___ 条 cognitive
   第2轮: ___ 条
   ...
   第10轮: ___ 条
   平均: ___ 条
   最大: ___ 条
   ```

### 信息密度评价标准
- 🟢 **好**: 平均 5~8，最大 ≤10；每条事件 subsystem 不同（memory/personality/emotion/response 各一条）
- 🟡 **可接受**: 平均 9~12；有 1~2 条重复 `memory.retrieved` 但不同 query 或不同 top_k → 明确告诉我"能接受这个密度"
- 🔴 **过载**: 平均 > 12；或者同一个 subsys 出现 5+ 次重复 → 不通过，先优化聚合再进 7.2.2

### 特别检查: 重复 memory.retrieved
如果看到某一轮里 **连续多条 purple bar** 都叫 `memory.retrieved`，点开每个的 payload 看 `query_hash`:
- hash 相同 → 代表同一次 search() 被内层多次调用触发 → 需要优化（记为 Bug）
- hash 不同 → 代表 Runtime 真的调了多次 search → 算"信息量大但可接受"

---

## ③ 性能基线对比（10 分钟）

> ⚠ 重要: **Phase 7.1 baseline 必须在 commit `ad20c6e` 上跑**，不是在当前 commit 上关 observer 跑，因为 Phase 7.2.1 已经改了 Memory/Personality hook。

### Step 1: 测 Phase 7.1 baseline（5 分钟）
```bash
git checkout ad20c6e  # pre-Phase4 baseline 带 Token 优化那版（Phase 7.1 之后的观察基线）
python api_server.py &
# 等 20 秒
python scripts/phase_7_2_1_observe_runner.py --perf --messages 50 --out data/phase721_observation/perf_phase71.json
git checkout eed2de6   # 回到 7.2.1
```
输出:
```
[Performance Phase 7.1]
  50 条消息:
    平均: ___.__ ms
    P50:  ___.__ ms
    P95:  ___.__ ms
    P99:  ___.__ ms
```

### Step 2: 测 Phase 7.2.1（5 分钟）
```bash
# 停掉上一个 api_server，重新启
python api_server.py &
# 等 20 秒
python scripts/phase_7_2_1_observe_runner.py --perf --messages 50 --out data/phase721_observation/perf_phase721.json
```
同样的输出。

### Step 3: 对比
```bash
python scripts/phase_7_2_1_observe_runner.py --perf-diff \
  --a data/phase721_observation/perf_phase71.json \
  --b data/phase721_observation/perf_phase721.json
```
输出:
```
[Performance Diff]
  平均:  Phase 7.1 = XXX ms → 7.2.1 = YYY ms  (差值: ±ZZ ms)
  P95:   Phase 7.1 = XXX ms → 7.2.1 = YYY ms  (差值: ±ZZ ms)
  评价:  PASS（< 50ms） / FAIL（≥ 50ms）
```

差值阈值（你定的）:
- 平均差 **< 20ms** → 理论性能 ✅
- 平均差 **20~50ms** → 能接受，但告诉我具体值
- 平均差 **≥ 50ms** → 不通过，进入专项排查（payload → JSON → sink → 前端，不要动核心模块）

---

## ④ 新架构 Bug 收集（随时）

观察全过程遇到的任何异常，只要满足 **"在 Phase 7.1 baseline 上没有，在 7.2.1 上新出现"** 就算 Bug。

常见候选（列个清单防漏）：
- [ ] Dashboard Timeline 渲染出错（JS 报错 / 红色条 / 空白卡）
- [ ] SSE 断开重连后 Timeline 丢事件（有 Last-Event-ID 应该续，没续 = Bug）
- [ ] `api_server.py` 控制台出现 [CognitiveHooks] 开头的 ERROR 日志（正常应该全是 debug）
- [ ] 某轮回复里，cognitive event 的 payload 里出现了 **完整的 memory text 或用户身份证/地址等敏感信息** → 高优先级泄露 Bug
- [ ] 正常聊天，回复为空 / 回复字符串不对（排除 LLM API 抽风 → 复现才有效）
- [ ] 任何"hook 崩溃"但业务返回值被改变（测试应该覆盖，但实机可能遇到没 mock 到的路径）

遇到任何一条，填到前面的观察验收单 ④ 里。

---

## 最终汇总模板（观察结束时复制粘贴给 Trae）

```
① Trace 连续性
- 连续消息: 通过 / 失败
- 多窗口并发: 通过 / 失败
- Runtime 重启: 通过 / 失败

② Timeline
- 单轮 cognitive event 数: 平均 / 最大
- 重复 memory.retrieved 次数:
- 信息密度评价:

③ 性能
Phase 7.1:
- 平均:
- P95:

Phase 7.2.1:
- 平均:
- P95:

差值:

④ 新架构 Bug
- 0 个 / 有（列出来）
```

---

## 通过条件（再贴一次）

- ① 三项全部通过
- ② 平均 ≤ 12 条，或 > 12 条你明确说"我能接受"
- ③ 平均差 < 50ms（如果 ≥ 50ms 但 P95 只差 10ms 以内可以放宽到"你判断"）
- ④ 0 个 Bug（1 个非关键 Bug 可以先修补丁 commit，再进入 7.2.2）

通过后 → 宣布 Phase 7.2.1 Observation Passed，Trae 直接在 `eed2de6` 之上做 Phase 7.2.2 Growth。
