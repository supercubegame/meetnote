# meetnote

把会议记录文本抽成结构化 JSON：参会人、决议、待办、负责人、截止日期。

后端走 StepFun 的 OpenAI 兼容接口（`step-3.7-flash`）。纯标准库，没有第三方依赖。

## 用法

```bash
export STEPFUN_API_KEY=...      # 只走环境变量或仓库 secrets
python -m meetnote.cli parse 会议记录.txt
cat 会议记录.txt | python -m meetnote.cli parse - --strict --out result.json
```

常用参数：`--today YYYY-MM-DD`（注入当天日期，让相对日期可复现）、`--strict`（结果未确认时
退出码 3）、`--diag`（带上只读诊断快照）、`--log FILE`（脱敏后的运行日志）。

## 输出

```json
{
  "status": "unconfirmed",
  "unconfirmed_reasons": ["owner_missing", "due_unparsed"],
  "participants": [{"name": "陈迪", "role": "产品", "unconfirmed": false, "reason": null}],
  "decisions": [{"text": "下一版本先做导入", "unconfirmed": false, "reason": null}],
  "action_items": [
    {"task": "补字段映射文档", "owner": null, "due_date": null, "due_date_raw": "尽快",
     "unconfirmed": true, "reasons": ["owner_missing", "due_unparsed"]}
  ],
  "counts": {"participants": 1, "decisions": 1, "action_items": 1}
}
```

拿不到的字段一律是 `null` 加一条原因，绝不静默丢掉。`status` 只有 `ok` 和 `unconfirmed` 两种。

## 开发

```bash
python verify.py         # 快闸门：零网络，全部离线断言
python verify_live.py    # 慢闸门：打一次真实接口，验响应结构有没有漂
```

约定和不变量写在 [AGENTS.md](AGENTS.md) 里，改代码前先读它。
