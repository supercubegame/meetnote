"""Pure core. No I/O, no clock, no unseeded randomness, no printing.

Every function here is deterministic: same input -> same output. The current
date is always injected as `today`. tests/test_meta.py enforces this with an
AST scan. See AGENTS.md.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

MODEL = "step-3.7-flash"
ENDPOINT = "https://api.stepfun.ai/step_plan/v1/chat/completions"

# --- coupled group: change one, recompute the others ---
# BACKOFF_CAP_MS must stay reachable: BASE * FACTOR**(MAX_RETRIES-1) == CAP.
# meta_backoff_pair_consistent asserts exactly that.
BACKOFF_BASE_MS = 500
BACKOFF_FACTOR = 2
MAX_RETRIES = 5
BACKOFF_CAP_MS = 8000

# The response envelope we depend on. Frozen on purpose: test_meta asserts this
# set by EQUALITY, so loosening it to make the live gate pass turns the fast
# gate red. Do not edit without editing meta_contract_paths_frozen.
REQUIRED_RESPONSE_PATHS = frozenset(
    {
        "id",
        "model",
        "choices",
        "choices[0].message.content",
        "choices[0].finish_reason",
        "usage.prompt_tokens",
        "usage.completion_tokens",
    }
)

_PATH_TYPES = {
    "id": str,
    "model": str,
    "choices": list,
    "choices[0].message.content": str,
    "choices[0].finish_reason": str,
    "usage.prompt_tokens": int,
    "usage.completion_tokens": int,
}

RESULT_KEYS = ("participants", "decisions", "action_items")


class ContentError(Exception):
    """Model output could not be turned into our schema at all."""


class EmptyInputError(Exception):
    """Nothing to send. Must never reach the transport."""


SYSTEM_PROMPT = (
    "你是会议记录结构化引擎。今天是 {today}。\n"
    "只输出一个 JSON 对象，不要 markdown 围栏，不要解释。\n"
    "顶层键必须是：participants, decisions, action_items。\n"
    "participants: [{{\"name\": 姓名(逐字保留原文), \"role\": 职能或 null}}]\n"
    "decisions: [决议文本]\n"
    "action_items: [{{\"task\": 待办事项, \"owner\": 负责人或 null, "
    "\"due_date\": 截止日期的原文表述或 null}}]\n"
    "不要发明信息。原文没写的字段一律用 null。"
)


def system_prompt(today: date) -> str:
    return SYSTEM_PROMPT.format(today=today.isoformat())


def build_request(notes: str, *, today: date, model: str = MODEL) -> dict:
    """Build the chat-completions payload. The API key is NEVER in here."""
    if not notes or not notes.strip():
        raise EmptyInputError("input_empty")
    return {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt(today)},
            {"role": "user", "content": notes},
        ],
    }


_SEG = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$")


def _resolve(obj, path):
    """Walk a dotted path with optional [i] indexes. Structural walk, not a
    regex over serialized data: the regex only ever sees one path segment."""
    cur = obj
    for part in path.split("."):
        m = _SEG.match(part)
        if not m:
            return False, None
        key, idx = m.group(1), m.group(2)
        if not isinstance(cur, dict) or key not in cur:
            return False, None
        cur = cur[key]
        if idx is not None:
            i = int(idx)
            if not isinstance(cur, list) or len(cur) <= i:
                return False, None
            cur = cur[i]
    return True, cur


def check_envelope(obj) -> list:
    """Return a list of contract problems. Empty list means the envelope is ok.

    Presence AND type AND non-blank are all checked, because a present-but-empty
    field would otherwise pass for free.
    """
    if not isinstance(obj, dict):
        return ["<root>:not_an_object"]
    problems = []
    for path in sorted(REQUIRED_RESPONSE_PATHS):
        found, val = _resolve(obj, path)
        if not found:
            problems.append(path + ":missing")
            continue
        expected = _PATH_TYPES.get(path)
        if expected is int:
            if isinstance(val, bool) or not isinstance(val, int):
                problems.append("%s:wrong_type(%s)" % (path, type(val).__name__))
                continue
        elif expected is not None and not isinstance(val, expected):
            problems.append("%s:wrong_type(%s)" % (path, type(val).__name__))
            continue
        if path == "choices" and not val:
            problems.append("choices:empty")
        if isinstance(val, str) and not val.strip():
            problems.append(path + ":blank")
    return problems


_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_MD = re.compile(r"^(\d{1,2})\s*月\s*(\d{1,2})\s*日?$")
_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_WK = re.compile(r"^(本|这|下)?\s*(?:周|星期)\s*([一二三四五六日天])$")


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def normalize_due(raw, *, today: date):
    """Return (iso_date_or_None, reason_or_None). Never raises, never guesses
    silently: anything it cannot resolve comes back with a reason attached.
    """
    if raw is None or not isinstance(raw, str) or not raw.strip():
        return None, "due_missing"
    s = raw.strip().replace("／", "/")
    m = _ISO.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d).isoformat(), None
        except ValueError:
            return None, "due_invalid_calendar_date"
    m = _MD.match(s) or _SLASH.match(s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        # Heuristic, documented in AGENTS.md: a bare month/day in the past is
        # read as next year rather than dropped.
        for year in (today.year, today.year + 1):
            try:
                cand = date(year, mo, d)
            except ValueError:
                return None, "due_invalid_calendar_date"
            if cand >= today:
                return cand.isoformat(), None
        return None, "due_invalid_calendar_date"
    if s == "今天":
        return today.isoformat(), None
    if s == "明天":
        return (today + timedelta(days=1)).isoformat(), None
    if s == "后天":
        return (today + timedelta(days=2)).isoformat(), None
    if s in ("月底", "本月底"):
        return _last_day_of_month(today.year, today.month).isoformat(), None
    if s == "下月底":
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        return _last_day_of_month(year, month).isoformat(), None
    m = _WK.match(s)
    if m:
        prefix, weekday_char = m.group(1), m.group(2)
        wd = _WEEKDAY[weekday_char]
        monday = today - timedelta(days=today.weekday())
        if prefix == "下":
            return (monday + timedelta(days=7 + wd)).isoformat(), None
        if prefix in ("本", "这"):
            return (monday + timedelta(days=wd)).isoformat(), None
        cand = monday + timedelta(days=wd)
        if cand < today:
            cand = cand + timedelta(days=7)
        return cand.isoformat(), None
    return None, "due_unparsed"


def _norm_participants(raw, problems):
    out = []
    if raw is None:
        problems.append("participants_missing")
        return out
    if not isinstance(raw, list):
        raise ContentError("participants_not_a_list")
    for item in raw:
        if isinstance(item, str):
            bad = not item.strip()
            out.append(
                {
                    "name": item,
                    "role": None,
                    "unconfirmed": bad,
                    "reason": "participant_name_missing" if bad else None,
                }
            )
        elif isinstance(item, dict):
            name = item.get("name")
            bad = not isinstance(name, str) or not name.strip()
            out.append(
                {
                    "name": name if isinstance(name, str) else None,
                    "role": item.get("role"),
                    "unconfirmed": bad,
                    "reason": "participant_name_missing" if bad else None,
                }
            )
        else:
            bad = True
            out.append(
                {"name": None, "role": None, "unconfirmed": True, "reason": "participant_unreadable"}
            )
        if out[-1]["unconfirmed"]:
            problems.append(out[-1]["reason"])
    return out


def _norm_decisions(raw, problems):
    out = []
    if raw is None:
        problems.append("decisions_missing")
        return out
    if not isinstance(raw, list):
        raise ContentError("decisions_not_a_list")
    for item in raw:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text") or item.get("decision")
        else:
            text = None
        bad = not isinstance(text, str) or not text.strip()
        out.append(
            {
                "text": text if isinstance(text, str) else None,
                "unconfirmed": bool(bad),
                "reason": "decision_text_missing" if bad else None,
            }
        )
        if bad:
            problems.append("decision_text_missing")
    return out


def _norm_actions(raw, problems, today):
    out = []
    if raw is None:
        problems.append("action_items_missing")
        return out
    if not isinstance(raw, list):
        raise ContentError("action_items_not_a_list")
    for item in raw:
        if not isinstance(item, dict):
            out.append(
                {
                    "task": None,
                    "owner": None,
                    "due_date": None,
                    "due_date_raw": None,
                    "unconfirmed": True,
                    "reasons": ["action_item_unreadable"],
                }
            )
            problems.append("action_item_unreadable")
            continue
        reasons = []
        task = item.get("task") or item.get("todo")
        if not isinstance(task, str) or not task.strip():
            task = None
            reasons.append("task_missing")
        owner = item.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            owner = None
            reasons.append("owner_missing")
        raw_due = item.get("due_date")
        iso, why = normalize_due(raw_due, today=today)
        if why:
            reasons.append(why)
        out.append(
            {
                "task": task,
                "owner": owner,
                "due_date": iso,
                "due_date_raw": raw_due if isinstance(raw_due, str) else None,
                "unconfirmed": bool(reasons),
                "reasons": reasons,
            }
        )
        problems.extend(reasons)
    return out


def parse_content(content, *, today: date, finish_reason: str = "stop") -> dict:
    """Turn model output into our schema.

    Hard rule: nothing is ever silently dropped or silently truncated. Every
    item survives into the result; anything doubtful is flagged unconfirmed with
    a reason. Structural impossibility raises ContentError instead.
    """
    if not isinstance(content, str):
        raise ContentError("content_not_a_string")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except ValueError:
        raise ContentError("content_not_json")
    if not isinstance(obj, dict):
        raise ContentError("content_not_an_object")

    problems = []
    warnings = []
    for key in obj:
        if key not in RESULT_KEYS:
            warnings.append("unknown_key:%s" % key)

    result = {
        "participants": _norm_participants(obj.get("participants"), problems),
        "decisions": _norm_decisions(obj.get("decisions"), problems),
        "action_items": _norm_actions(obj.get("action_items"), problems, today),
        "extras": {k: v for k, v in obj.items() if k not in RESULT_KEYS},
        "warnings": warnings,
    }
    if finish_reason and finish_reason != "stop":
        problems.append("truncated:finish_reason=%s" % finish_reason)
    reasons = []
    for p in problems:
        if p not in reasons:
            reasons.append(p)
    result["status"] = "unconfirmed" if reasons else "ok"
    result["unconfirmed_reasons"] = reasons
    result["counts"] = {k: len(result[k]) for k in RESULT_KEYS}
    return result
