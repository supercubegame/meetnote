# meetnote

把会议记录文本抽成结构化 JSON：参会人、决议、待办、负责人、截止日期。

后端走 StepFun 的 OpenAI 兼容接口（`step-3.7-flash`）。纯标准库，没有第三方依赖。

## 当前使用状态（2026-09-07）

用户明确表示暂不使用会议提取功能。**自动真实接口测试已停用，不要求购买 StepFun 订阅。** push、PR 和每日定时只跑不访问接口的代码测试与报告；默认手动运行同样不调用 StepFun。

若以后决定验证真实服务，在 Actions 的 verify 中手动勾选 `run_live`；这会使用现有 secret 发起真实请求，可能产生费用。本次停用没有修改密钥、支付、接口实现或历史告警，也没有证明服务恢复。

`.github/live-heartbeat.json` 保留为历史记录，不再定时更新。停用期间不能把它变旧误判为意外故障，也不能把快测试成功当成真实接口健康。历史 issue #4 保留，不自动关闭。手动 live 测试不写定时心跳、不自动修改该历史告警。

## 用法（仅在以后主动使用时）

```bash
export STEPFUN_API_KEY=...      # 只走环境变量或仓库 secrets
python -m meetnote.cli parse 会议记录.txt
cat 会议记录.txt | python -m meetnote.cli parse - --strict --out result.json
```

常用参数：`--today YYYY-MM-DD`（注入当天日期，让相对日期可复现）、`--strict`（结果未确认时退出码 3）、`--diag`（只读诊断快照）、`--log FILE`（脱敏日志）。直接运行 CLI 或 `verify_live.py` 仍会调用接口，CI 开关不禁用这些显式本地命令。

## 输出

```json
{
  "status": "unconfirmed",
  "unconfirmed_reasons": ["owner_missing", "due_unparsed"],
  "participants": [{"name": "陈迪", "role": "产品", "unconfirmed": false, "reason": null}],
  "decisions": [{"text": "下一版本先做导入", "unconfirmed": false}],
  "action_items": [
    {"task": "补字段映射文档", "owner": null, "due_date": null, "due_date_raw": "尽快",
     "unconfirmed": true, "reasons": ["owner_missing", "due_unparsed"]}
  ],
  "counts": {"participants": 1, "decisions": 1, "action_items": 1}
}
```

拿不到的字段一律是 `null` 加原因，绝不静默丢掉。`status` 只有 `ok` 和 `unconfirmed` 两种。

## 开发

```bash
python verify.py                   # 快闸门：零网络
python checks/live_opt_in_check.py  # 检查真实 workflow 的手动开关与预期计算，不发请求
python verify_live.py              # 显式真实接口测试，不要当作本地自测运行
```

约定和不变量写在 [AGENTS.md](AGENTS.md) 里，改代码前先读它。
