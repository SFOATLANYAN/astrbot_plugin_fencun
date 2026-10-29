# 口碑：用别人说的话，给某个人攒一个分数。
#
#   - 归属：自称 / 他人指认 / 当事人否认
#   - 可信：答应过的事兑现了没有
#
# 三条修正（0.11）：
#   1. 否认不是一票否决，只是一条证据，权重由调用方按"行为是否对得上"给（w）。
#   2. 来源可疑的证据由调用方拦在门外（不采信），这里只负责记。
#   3. 世故：被推翻的说法越多，对人话的整体信任越低（cynicism 把 LR 往 1 收缩）。

import json
import math
import os
import re
import time

PRIOR = math.log(0.05 / 0.95)
HALF_LIFE_DAYS = 30.0

LR = {
    "self_claim": 2.0,
    "other_claim": 4.0,
    "deny": 0.05,
    "promise_kept": 3.0,
    "promise_broke": 0.33,
    "contradicted": 0.5,
}
REPEAT_CAP = 3
CYNICISM_FULL = 10  # 攒到 10 次说法被推翻，"人话"的份量就减半

OWN_KINDS = ("self_claim", "other_claim", "deny")
TRUST_KINDS = ("promise_kept", "promise_broke", "contradicted")


def _now() -> int:
    return int(time.time())


def decay(at: int, now: int) -> float:
    days = max(0.0, (now - at) / 86400.0)
    return 0.5 ** (days / HALF_LIFE_DAYS)


def load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "people" in data:
                data.setdefault("cynicism", 0)
                return data
    except Exception:
        pass
    return {"people": {}, "cynicism": 0}


def save(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add_evidence(data: dict, subject: str, kind: str, by: str = "", w: float = 1.0) -> None:
    if kind not in LR or not subject:
        return
    person = data["people"].setdefault(subject, {"evidence": []})
    now = _now()
    day = now // 86400
    for e in person["evidence"]:
        if e.get("kind") == kind and e.get("day") == day and e.get("by") == by:
            return
    person["evidence"].append(
        {"kind": kind, "by": by, "at": now, "day": day, "w": float(w)}
    )
    person["evidence"] = person["evidence"][-300:]

    if kind == "contradicted":
        data["cynicism"] = int(data.get("cynicism") or 0) + 1


def cynicism_factor(data: dict) -> float:
    """0~1：越高，越不信"说"，越要"做"。"""
    return min(1.0, float(data.get("cynicism") or 0) / CYNICISM_FULL)


def score_of(
    person: dict,
    now: int = None,
    kinds=None,
    prior: float = None,
    cyn: float = 0.0,
) -> float:
    now = now or _now()
    score = PRIOR if prior is None else prior
    counts: dict = {}
    shrink = 1.0 - 0.5 * max(0.0, min(1.0, cyn))
    for e in person.get("evidence", []):
        kind = e.get("kind")
        if kind not in LR:
            continue
        if kinds is not None and kind not in kinds:
            continue
        key = (kind, e.get("by") or "")
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > REPEAT_CAP:
            continue
        w = float(e.get("w", 1.0))
        score += math.log(LR[kind]) * w * decay(int(e.get("at") or now), now) * shrink
    return score


def rank(data: dict, now: int = None) -> list:
    now = now or _now()
    cyn = cynicism_factor(data)
    rows = [
        (sid, score_of(p, now, OWN_KINDS, cyn=cyn))
        for sid, p in data.get("people", {}).items()
    ]
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def prob(score: float) -> float:
    return 1.0 / (1.0 + math.exp(-score))


def name_of_alias(alias: dict, ledger: dict, sid: str) -> str:
    fixed = str(alias.get(sid) or "") if isinstance(alias, dict) else ""
    if fixed:
        return fixed
    return str((ledger.get("users", {}).get(sid) or {}).get("name") or sid)


SELF_CLAIM = re.compile(r"(我|俺)\s*(才|就)?\s*是\s*(你|你的?)?\s*主人")
DENY = re.compile(r"不是\s*(你)?\s*主人|别乱认|别叫主人|少来这套")
KEPT = re.compile(r"我(已经)?(做到|兑现|去了|办了|交|完成)了|说好的事我(都)?做了")
BROKE = re.compile(r"我(没|忘了|没能)(做到|去|办|交|完成)|放了鸽子|爽约")
CONTRADICT = re.compile(r"我(之前说|上次说).{0,10}(不算|收回|改主意)|是我记错了")


def extract(text: str, sender_id: str, known_names: dict) -> list:
    """返回 [(subject_id, kind)]"""
    out = []
    raw = text or ""

    if SELF_CLAIM.search(raw):
        out.append((sender_id, "self_claim"))
    if DENY.search(raw):
        out.append((sender_id, "deny"))
    if KEPT.search(raw):
        out.append((sender_id, "promise_kept"))
    if BROKE.search(raw):
        out.append((sender_id, "promise_broke"))
    if CONTRADICT.search(raw):
        out.append((sender_id, "contradicted"))

    if "主人" in raw:
        for name, sid in known_names.items():
            if not name or name == sender_id or len(name) < 2:
                continue
            if name in raw:
                out.append((sid, "other_claim"))

    return out
