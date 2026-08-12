# meetnote 项目规矩

命令行工具：把会议记录文本抽成结构化 JSON（参会人 / 决议 / 待办 / 负责人 / 截止日期）。
后端是 StepFun 的 OpenAI 兼容 chat/completions 接口，模型 `step-3.7-flash`。

## 结构

- `meetnote/core.py` 纯核心：prompt 构建、响应信封校验、模型输出解析、日期归一化。
- `meetnote/client.py` 传输层：错误分类、重试、退避。transport 可注入。
- `meetnote/output.py` 唯一的输出闸口：所有 stdout / stderr / 日志 / 落盘都过脱敏。
- `meetnote/cli.py` 命令行入口，所有 I/O 都在这里。
- `meetnote/live_check.py` 慢闸门逻辑：唯一能发现真实响应漂移的地方。
- `verify.py` 快闸门（零网络零依赖）。`verify_live.py` 慢闸门（打一次真实 API）。
- `tests/fixtures/*.json` 录制的假响应。**它们永远符合我们自己的预期，所以它们证明不了真实接口没变。**

## 命令

```
python verify.py                 # 快闸门，零网络，退出码 0/1
python verify_live.py            # 慢闸门，退出码 0 / 1(漂移) / 78(未确认)
python -m meetnote.cli parse tests/fixtures/notes_zh.txt --today 2026-08-12 --diag
MEETNOTE_STUB_FILE=tests/fixtures/ok.json python -m meetnote.cli parse - --today 2026-08-12
```

**改完任何代码，必须跑 `python verify.py`，绿了才算完。**

## 铁律

1. **`core.py` 保持纯。** 不读文件、不打印、不碰环境变量、不用系统时间、不用未播种随机。
   当天日期一律由调用方以 `today=` 注入。理由：同样输入必然同样输出，几万条用例毫秒级跑完，
   同一份逻辑换任何外壳都能复用。`test_core_imports_are_pure` 用 AST 扫描守着这条，
   想图省事塞一个 `date.today()` 会直接红。
2. **不许静默丢弃、静默截断、静默跳过。** 任何提前放弃的路径，结果里必须带 `unconfirmed`
   标记和具体原因。模型少给字段、日期解析不出来、`finish_reason` 不是 `stop`,全都留在结果里
   打标，不是删掉。`parse_content` 的计数断言守着这条。
3. **密钥只走环境变量 / 仓库 secrets。** 代码、日志、报告、artifact 里都不能出现。
   唯一上线的位置是 `Client.build_headers()`。真实的泄漏路径是**接口把我们的 Authorization
   头回显在错误体里**,所以所有出口都过 `Redactor`，并且有一条端到端断言用随机哨兵值验证
   四个出口（stdout / stderr / 日志文件 / 落盘文件）都是 0 次出现。
4. **「接口挂了」和「接口变了」是两件事，永远不能合并。**
   - 5xx / 429 / 连接错误 / 401 / 403 → `AvailabilityError`,我们什么都没学到，标未确认，可重试。
   - 拒绝我们请求形状的 4xx，或者 2xx 但响应体不符合 `REQUIRED_RESPONSE_PATHS` → `ContractError`,必须红。
   把 401 归到「未确认」而不是「漂移」是有意的：凭据问题不代表接口变了。
5. **退出码是契约**，端到端断言逐个验过：
   `0` 出了结果（可能是 unconfirmed，那也是一个带标签的结果）· `2` 用法错误 ·
   `3` `--strict` 且结果未确认 · `4` 无法确认（接口不可用 / 凭据被拒）·
   `5` 契约漂移 · `6` 模型输出压根构不成我们的 schema。
6. **诊断出口 `DIAG_FIELDS` 只读，字段可以加，不能改名不能删。** 端到端断言按名字读它们，
   重构改名会让闸门变哑。`test_diag_field_names_are_stable` 用等号钉住。
7. **`REQUIRED_RESPONSE_PATHS` 用等号钉住（7 条）。** 为了让慢闸门通过而放宽它，会让快闸门红。
   这是故意的：能被随手放宽的契约不是契约。
8. **测试条数是等号，不是下限。** 下限会自己漂：加了测试数字不动，几轮之后文档和实际对不上。
   加减测试要在同一个提交里改 `tests/expected_counts.py`。
9. **本文件 200 行上限由断言守着**，不是靠这句话。写长了模型会开始忽略里面的指令。

## 耦合参数组：改一个必须重算另一个

- `BACKOFF_BASE_MS` × `BACKOFF_FACTOR` × `MAX_RETRIES` × `BACKOFF_CAP_MS`：
  必须满足 `BASE * FACTOR**(MAX_RETRIES-1) == CAP`，否则那个上限从写下那天起就碰不到,
  一个够不着的边界和一条永远为真的断言是同一个洞。`test_backoff_cap_is_reachable_by_construction`
  在守。默认值：500 / 2 / 5 / 8000，等待序列 500-1000-2000-4000-8000。
  用 `--retry-budget` 调小时上限会变得碰不到，这是显式选择，不是默认路径。
- `--backoff-scale` 只用来让离线闸门别真等 15 秒。产品默认值不许为了迁就测试时长而改。

## 慢闸门为什么必须存在

录制的假响应是我们自己写的，它永远符合我们自己的预期。所以快闸门再绿，也只能证明
「我们的解析器和我们的录音是一致的」。真实响应结构漂移只有慢闸门能发现。

为了防止慢闸门退化成「验证我们自己的录音」，它带一组回放守卫，任何一条不成立直接判红：

- `stub_channel_absent`：`MEETNOTE_STUB_FILE` 一旦被设置立刻红。
- `transport_is_real`：transport 必须是 `UrllibTransport`。
- `endpoint_is_real`：必须打 `core.ENDPOINT` 且是 https。
- `contract_not_emptied`：必须真有 7 条待校验路径,先证明「解析到了东西」，再断言里面有什么。
- `response_is_not_a_recording`：真实响应 id 不能带 `fixture-` 前缀，也不能撞上任何录音 id。
  所有 fixture 的 id 都必须带这个前缀，由 `test_fixture_ids_carry_prefix` 守着，
  否则这条守卫就变成空断言。

三档结果，含义不同：

- 退出码 `0`：真实响应符合契约。
- 退出码 `1`：漂移。信封缺字段 / 类型变了 / 接口拒了我们的请求形状 / 回放守卫失败。
- 退出码 `78`：**未确认**。没有 secret、接口不可用、凭据被拒。CI 里不判红，但报告里明写
  「本次没有验证任何真实响应」,绝不当成通过。

**新增字段只报告不判红**（`envelope_additions`），加字段是正常演进；少字段和改类型才是漂移。

## 这些是真的测不出来的，别去补那个补不上的洞

- 模型语义质量。它把「下周五」理解成哪天，我们能验；它有没有漏掉一条决议，机器判不了。
  `nonce_echoed_by_model` 和 `probe_yielded_extractions` 是**软信号**（不判红，只标未确认）,
  硬化它们只会让闸门开始抛硬币。
- `elapsed_ms` 只是证据，不是断言。CI 机器很快，在这里设阈值就是装饰。
- 「接口挂了」和「接口在维护窗口返回了合法但空的响应」在可观测层面可以完全一致。
  我们能断言的是行为本身：触发了兜底、标了未确认。

## CI 与报告

两个并行 job（`fast` 离线 / `live` 打真实接口），第三个 `report` job 把两份报告合并写回评论。

- 有 PR 时写 PR 评论，没 PR 时写 commit 评论,**两条路都实现，都用 marker 去重**。
  只挂在 PR 事件上的话，主干挂了就没人看得见。
- **回写失败必须让 job 变红。** 报告没送达等于这次没跑。写完还会回读一次确认评论真的存在。
- 报告自带证据：失败的测试名、期望值与实际值、日志尾巴、慢闸门的响应 id 与计数。
  判断标准只有一条:只看那条评论，能不能定位到根因。

## 人工环节（agent 碰不到）

- 仓库 secrets 里的 `STEPFUN_API_KEY`。
- Actions 的工作流写权限（不开的话报告传不回来，job 会直接红）。
- 「输出读起来对不对」这类验收。机器只判结构，不判好用。
