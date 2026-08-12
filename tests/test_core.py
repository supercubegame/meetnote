"""Core is pure, so these run in milliseconds and need no fixtures.

TODAY is a Wednesday on purpose: the weekday arithmetic below is only
meaningful if "this week" and "next week" can actually differ.
"""
import unittest
from datetime import date

from meetnote import core

TODAY = date(2026, 8, 12)  # Wednesday, weekday() == 2

GOOD_ENVELOPE = {
    "id": "resp-1",
    "model": "step-3.7-flash",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
}


def envelope(**overrides):
    import copy

    env = copy.deepcopy(GOOD_ENVELOPE)
    env.update(overrides)
    return env


class BuildRequest(unittest.TestCase):
    def test_build_request_shape(self):
        payload = core.build_request("张三说要做导入", today=TODAY)
        self.assertEqual(payload["model"], "step-3.7-flash")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][1]["content"], "张三说要做导入")

    def test_build_request_rejects_empty(self):
        for bad in ("", "   ", "\n\t"):
            with self.assertRaises(core.EmptyInputError):
                core.build_request(bad, today=TODAY)

    def test_system_prompt_carries_today(self):
        prompt = core.system_prompt(TODAY)
        self.assertIn("2026-08-12", prompt)
        self.assertIn("participants", prompt)
        self.assertIn("action_items", prompt)


class NormalizeDue(unittest.TestCase):
    def test_normalize_due_iso(self):
        self.assertEqual(core.normalize_due("2026-09-01", today=TODAY), ("2026-09-01", None))

    def test_normalize_due_chinese_month_day(self):
        self.assertEqual(core.normalize_due("8月20日", today=TODAY), ("2026-08-20", None))
        self.assertEqual(core.normalize_due("9月1", today=TODAY), ("2026-09-01", None))

    def test_normalize_due_past_month_day_rolls_to_next_year(self):
        self.assertEqual(core.normalize_due("1月5日", today=TODAY), ("2027-01-05", None))

    def test_normalize_due_slash(self):
        self.assertEqual(core.normalize_due("9/1", today=TODAY), ("2026-09-01", None))

    def test_normalize_due_relative_days(self):
        self.assertEqual(core.normalize_due("今天", today=TODAY), ("2026-08-12", None))
        self.assertEqual(core.normalize_due("明天", today=TODAY), ("2026-08-13", None))
        self.assertEqual(core.normalize_due("后天", today=TODAY), ("2026-08-14", None))

    def test_normalize_due_month_end(self):
        self.assertEqual(core.normalize_due("月底", today=TODAY), ("2026-08-31", None))
        self.assertEqual(core.normalize_due("下月底", today=TODAY), ("2026-09-30", None))
        self.assertEqual(core.normalize_due("下月底", today=date(2026, 12, 3)), ("2027-01-31", None))

    def test_normalize_due_weekdays(self):
        self.assertEqual(core.normalize_due("下周五", today=TODAY), ("2026-08-21", None))
        self.assertEqual(core.normalize_due("本周一", today=TODAY), ("2026-08-10", None))
        self.assertEqual(core.normalize_due("周三", today=TODAY), ("2026-08-12", None))
        self.assertEqual(core.normalize_due("周一", today=TODAY), ("2026-08-17", None))
        self.assertEqual(core.normalize_due("星期日", today=TODAY), ("2026-08-16", None))

    def test_normalize_due_invalid_calendar_date(self):
        self.assertEqual(core.normalize_due("2026-02-30", today=TODAY)[1], "due_invalid_calendar_date")
        self.assertEqual(core.normalize_due("2月30日", today=TODAY)[1], "due_invalid_calendar_date")
        self.assertEqual(core.normalize_due("13月1日", today=TODAY)[1], "due_invalid_calendar_date")

    def test_normalize_due_missing(self):
        for bad in (None, "", "   ", 42, ["2026-01-01"]):
            self.assertEqual(core.normalize_due(bad, today=TODAY), (None, "due_missing"))

    def test_normalize_due_unparsed(self):
        for bad in ("尽快", "看情况", "Q3", "下个季度"):
            self.assertEqual(core.normalize_due(bad, today=TODAY), (None, "due_unparsed"))


class ParseContent(unittest.TestCase):
    def test_parse_content_happy_path(self):
        content = (
            '{"participants": [{"name": "陈迪", "role": "产品"}],'
            ' "decisions": ["先做导入"],'
            ' "action_items": [{"task": "补文档", "owner": "陈迪", "due_date": "8月20日"}]}'
        )
        result = core.parse_content(content, today=TODAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["unconfirmed_reasons"], [])
        self.assertEqual(result["counts"], {"participants": 1, "decisions": 1, "action_items": 1})
        self.assertEqual(result["participants"][0]["name"], "陈迪")
        self.assertEqual(result["decisions"][0]["text"], "先做导入")
        item = result["action_items"][0]
        self.assertEqual(item["due_date"], "2026-08-20")
        self.assertEqual(item["due_date_raw"], "8月20日")
        self.assertFalse(item["unconfirmed"])

    def test_parse_content_strips_code_fence(self):
        fenced = '```json\n{"participants": [], "decisions": [], "action_items": []}\n```'
        result = core.parse_content(fenced, today=TODAY)
        self.assertEqual(result["status"], "ok")

    def test_parse_content_flags_missing_owner(self):
        content = (
            '{"participants": [], "decisions": [],'
            ' "action_items": [{"task": "补文档", "owner": null, "due_date": "8月20日"}]}'
        )
        result = core.parse_content(content, today=TODAY)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("owner_missing", result["unconfirmed_reasons"])
        self.assertTrue(result["action_items"][0]["unconfirmed"])
        self.assertEqual(result["action_items"][0]["task"], "补文档")

    def test_parse_content_flags_bad_due_date(self):
        content = (
            '{"participants": [], "decisions": [],'
            ' "action_items": [{"task": "补文档", "owner": "陈迪", "due_date": "2026-02-30"}]}'
        )
        result = core.parse_content(content, today=TODAY)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("due_invalid_calendar_date", result["unconfirmed_reasons"])
        self.assertIsNone(result["action_items"][0]["due_date"])
        self.assertEqual(result["action_items"][0]["due_date_raw"], "2026-02-30")

    def test_parse_content_keeps_unknown_keys(self):
        content = (
            '{"participants": [], "decisions": [], "action_items": [],'
            ' "summary": "模型自己加的字段"}'
        )
        result = core.parse_content(content, today=TODAY)
        self.assertEqual(result["warnings"], ["unknown_key:summary"])
        self.assertEqual(result["extras"], {"summary": "模型自己加的字段"})

    def test_parse_content_flags_truncation(self):
        content = '{"participants": [], "decisions": [], "action_items": []}'
        result = core.parse_content(content, today=TODAY, finish_reason="length")
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("truncated:finish_reason=length", result["unconfirmed_reasons"])

    def test_parse_content_never_drops_items(self):
        content = (
            '{"participants": [{"name": "陈迪"}, {"role": "后端"}, 7],'
            ' "decisions": ["先做导入", ""],'
            ' "action_items": [{"task": "a", "owner": "陈迪", "due_date": "8月20日"},'
            ' {"task": "", "owner": null, "due_date": "尽快"}, "随手写的一行"]}'
        )
        result = core.parse_content(content, today=TODAY)
        self.assertEqual(result["counts"], {"participants": 3, "decisions": 2, "action_items": 3})
        self.assertEqual(result["status"], "unconfirmed")
        self.assertTrue(result["participants"][2]["unconfirmed"])
        self.assertTrue(result["decisions"][1]["unconfirmed"])
        self.assertEqual(result["action_items"][2]["reasons"], ["action_item_unreadable"])

    def test_parse_content_rejects_non_json(self):
        with self.assertRaises(core.ContentError) as ctx:
            core.parse_content("当然可以！这次会议讨论了导入问题。", today=TODAY)
        self.assertEqual(str(ctx.exception), "content_not_json")

    def test_parse_content_rejects_non_object(self):
        with self.assertRaises(core.ContentError) as ctx:
            core.parse_content("[1, 2, 3]", today=TODAY)
        self.assertEqual(str(ctx.exception), "content_not_an_object")

    def test_parse_content_rejects_non_string(self):
        with self.assertRaises(core.ContentError) as ctx:
            core.parse_content(None, today=TODAY)
        self.assertEqual(str(ctx.exception), "content_not_a_string")

    def test_parse_content_rejects_wrong_container_types(self):
        cases = {
            '{"participants": "陈迪"}': "participants_not_a_list",
            '{"decisions": "先做导入"}': "decisions_not_a_list",
            '{"action_items": {"task": "a"}}': "action_items_not_a_list",
        }
        for content, expected in cases.items():
            with self.assertRaises(core.ContentError) as ctx:
                core.parse_content(content, today=TODAY)
            self.assertEqual(str(ctx.exception), expected)

    def test_parse_content_participants_as_strings(self):
        content = '{"participants": ["陈迪", "林岚"], "decisions": [], "action_items": []}'
        result = core.parse_content(content, today=TODAY)
        self.assertEqual([p["name"] for p in result["participants"]], ["陈迪", "林岚"])
        self.assertEqual(result["status"], "ok")

    def test_parse_content_missing_top_level_keys_are_flagged(self):
        result = core.parse_content("{}", today=TODAY)
        self.assertEqual(result["status"], "unconfirmed")
        for reason in ("participants_missing", "decisions_missing", "action_items_missing"):
            self.assertIn(reason, result["unconfirmed_reasons"])


class CheckEnvelope(unittest.TestCase):
    def test_check_envelope_accepts_good(self):
        self.assertEqual(core.check_envelope(envelope()), [])

    def test_check_envelope_missing_field(self):
        env = envelope()
        del env["usage"]["completion_tokens"]
        self.assertEqual(core.check_envelope(env), ["usage.completion_tokens:missing"])

    def test_check_envelope_missing_nested_path(self):
        env = envelope()
        del env["choices"][0]["message"]
        self.assertEqual(core.check_envelope(env), ["choices[0].message.content:missing"])

    def test_check_envelope_wrong_type(self):
        env = envelope()
        env["choices"][0]["message"]["content"] = 42
        self.assertEqual(core.check_envelope(env), ["choices[0].message.content:wrong_type(int)"])

    def test_check_envelope_blank_content(self):
        env = envelope()
        env["choices"][0]["message"]["content"] = "   "
        self.assertEqual(core.check_envelope(env), ["choices[0].message.content:blank"])

    def test_check_envelope_empty_choices(self):
        env = envelope(choices=[])
        problems = core.check_envelope(env)
        self.assertIn("choices:empty", problems)
        self.assertIn("choices[0].message.content:missing", problems)
        self.assertIn("choices[0].finish_reason:missing", problems)

    def test_check_envelope_rejects_bool_token_count(self):
        env = envelope()
        env["usage"]["prompt_tokens"] = True
        self.assertEqual(core.check_envelope(env), ["usage.prompt_tokens:wrong_type(bool)"])

    def test_check_envelope_rejects_non_object(self):
        self.assertEqual(core.check_envelope([]), ["<root>:not_an_object"])
        self.assertEqual(core.check_envelope("ok"), ["<root>:not_an_object"])

    def test_check_envelope_reports_every_problem_at_once(self):
        env = {"choices": [{}]}
        problems = core.check_envelope(env)
        self.assertGreaterEqual(len(problems), 5)
        self.assertIn("id:missing", problems)
        self.assertIn("model:missing", problems)


if __name__ == "__main__":
    unittest.main()
