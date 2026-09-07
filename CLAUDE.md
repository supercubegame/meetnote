# meetnote 项目规矩

命令行工具：把会议记录文本抽成结构化 JSON（参会人 / 决议 / 待办 / 负责人 / 截止日期）。
后端是 StepFun 的 OpenAI 兼容 chat/completions 接口，模型 `step-3.7-flash`。

## 当前状态：暂不使用（2026-09-07）

用户明确不使用会议提取功能。自动真实接口测试已停用，不要求恢复或购买订阅。
`push`、`pull_request`、`schedule` 仅运行不访问接口的测试和报告。
只有 `workflow_dispatch` 且布尔输入 `run_live` 为 true 时才运行 live job。
默认 false；不把跳过描述为接口健康或恢复。手动真实请求可能产生费用。
直接运行 CLI 和 `verify_live.py` 仍会调用接口，不受 CI 开关限制。

## 结构

- `meetnote/core.py`：纯核心，prompt、信封校验、模型输出和日期归一化。
- `meetnote/client.py`：传输层，错误分类、重试、退避，transport 可注入。
- `meetnote/output.py`：唯一输出闸口，stdout / stderr / 日志 / 文件均脱敏。
- `meetnote/cli.py`：命令行 I/O；生产功能未因停用 CI 自动请求而被删除。
- `meetnote/live_check.py`、`verify_live.py`：真实接口验收逻辑，保留不弱化。
- `tests/fixtures/*.json`：假响应只证明本地行为，不证明外部接口没变化。
- `verify.py`：零网络快闸门。每次代码修改后必须运行。
- `checks/live_opt_in_check.py`：真实 workflow 条件与预期 shell 的无网络反例。

## 命令

```bash
python verify.py
python checks/live_opt_in_check.py
python checks/report_identity_check.py
# 仅在用户主动要求真实请求时运行：
python verify_live.py
```

## 铁律

1. core 保持纯：不读文件、不打印、不碰环境、不用系统时间和随机数。
   当天日期由 `today=` 注入；AST 断言守导入、时钟、随机与输出调用。
2. 不静默丢弃、截断或跳过。缺字段用 null、unconfirmed 和明确原因，不冒充完整。
3. 密钥只走环境变量或 secrets，`Client.build_headers()` 是上线位置。
   接口回显 Authorization 也是泄漏路径；四个输出出口必须过 Redactor。
   随机哨兵端到端检查 stdout、stderr、日志、落盘；测试不得写死真实密钥。
4. 接口不可用与契约漂移分开。401/403、429/5xx、连接错误不是成功响应。
   已知 subscription_required 表示真实响应未确认，不得改写成恢复。
   未知请求拒绝或响应契约不符仍按原错误分类处理，不能为了绿色放宽契约。
5. CLI 退出码保留：0 输出结果，2 用法错误，3 strict 未确认，4 无法确认，5 契约漂移，6 schema 失败。
6. `DIAG_FIELDS` 诊断出口只读，字段不删不改名；集合等号断言保留。
7. `REQUIRED_RESPONSE_PATHS` 保持 7 条等号契约，不为通过而放宽。
8. 测试数由源码派生并与 `tests/expected_counts.py` 相等；保留原检查，不靠减数变绿。
9. AGENTS.md 与 CLAUDE.md 逐字节相同且不超过 200 行。

## 耦合参数

- `BACKOFF_BASE_MS` × `BACKOFF_FACTOR` × `MAX_RETRIES` × `BACKOFF_CAP_MS`：
  `BASE * FACTOR**(MAX_RETRIES-1) == CAP`。默认 500 / 2 / 5 / 8000。
  等待 500、1000、2000、4000、8000ms；显式 retry-budget 可缩小预算。
- backoff-scale 仅让假响应测试不必真的等待，不改产品参数迁就测试时间。
- live job 条件与 report 的 expect shell 必须同改。手动 true 才应运行，两边不符则失败。
- 实际 shell 的 16 个事件/输入组合在独立检查中执行，不访问外部接口。
- report marker、composer 哨兵、运行身份与独立读回字段保持原契约。

## 真实接口验收边界

慢闸门保留是因为录音证明不了外部响应结构。以后启用时必须保留：
`stub_channel_absent`、`transport_is_real`、`endpoint_is_real`、`contract_not_emptied`、`response_is_not_a_recording`。
fixture 响应 id 带前缀，真实响应不得撞录音；`tests/test_live_gate.py` 保留各分支假 transport 测试。
退出码 0 是真实契约通过，1 是漂移，78 是未确认，不等同于恢复。
新增字段只报告，不当成契约失败。模型语义质量、是否漏决议不能用结构测试证明。
nonce 回显和提取产出是软信号，不把它们硬化成抛硬币的红绿闸门。

## 历史心跳与告警

`.github/live-heartbeat.json` 作为历史记录保留，checked_at/status 不手改。
过去每日 live 测试写此心跳；现在每日仅跑快测试，该心跳不会继续刷新。
停用期间不能把它超过 48 小时当成意外停机；也不能伪造新时间证明活跃。
旧的 schedule-only 心跳和告警恢复代码保留，但因 live 仅手动运行而不可达。
手动 live 不刷新定时心跳，也不自动关闭历史 issue #4。
历史告警保持开放不代表用户必须买订阅；状态是功能未使用，不是已恢复。
外部巡检需结合本节停用记录，不得仅凭旧 heartbeat 要求重启真实接口测试。
本轮未修改任何 live Agent 配置，不能声称外部 Agent 已自动调整巡检逻辑。
以后若恢复自动监测，须同时审查触发策略、心跳、报告预期、告警状态及外部观察者。
GitHub 定时可能因长期不活跃停用；配置存在不等于实际运行。

## CI 与报告

三个 job：fast、live、report。fast 与 report 正常运行，live 默认按计划跳过。
报告必须写“功能暂不使用，真实响应没有被验证”，不能把 fast 通过包装成服务恢复。
PR 事件独占 PR 评论；push、schedule 和手动事件写 commit 评论，防互相覆盖。
报告发送后独立读回完整正文、作者、目标与本次身份；回写失败必须报红。
不启用 live 时不查询或改变历史告警；恢复守卫及其正反测试仍保留。
fast 的多条检查命令使用 set -e 和 pipefail，前置断言失败不能被后续成功覆盖。
报告应保留失败测试名、期望/实际和日志尾巴，便于定位真实原因。

## 合并与人工边界

使用 squash，旧工作分支不硬解冲突，后续从最新 main 开分支。
支付、密钥、仓库设置与 live Agent 修改需用户授权，不能由功能停用推断授权。
不删除历史告警、心跳、分支或测试资产，不在本轮请求真实 StepFun 响应。
