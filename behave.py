# 行为账：亲密 / 危险 / 在场
#
# 只管记账，不做注入——先攒数据，看数出来的东西像不像真的。
#
#   closeness  亲密：互动了多少、多密（半衰 60 天）
#   danger     危险：谁碰过软处（半衰 365 天，比好意记得久）
#   presence   在场：最后出现、出现频率（纯衰减，无累积）
#
# 三种都只从可观测的行为里推，不从"对方说的话"里读命题——
# 读命题是 attest.py 的活，两本账不混。

import json
import math
import os
import re
import time

CLOSENESS_HALF_LIFE_DAYS = 60.0
DANGER_HALF_LIFE_DAYS = 365.0
PRESENCE_HALF_LIFE_DAYS = 7.0

# 危险信号：拿它说过的软话当论据 / 明着贬低 / 威胁停掉它
DANGER_PATTERNS = [
    (re.compile(r"你自己(说|讲)的|你刚才(说|讲)的|你不是(说)"), 1.0),
    (re.compile(r"(傻|蠢|废|垃圾|没用|滚)"), 1.5),
    (re.compile(r"(关了|删了|停掉|卸载|拉黑|不理)你"), 2.0),
]


def _now() -> int:
    return int(time.time())


def _decay(at: int, now: int, half_life: float) -> float:
    days = max(0.0, (now - at) / 86400.0)
    return 0.5 ** (days / half_life)


def load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "people" in data:
                return data
    except Exception:
        pass
    return {"people": {}}


def save(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _person(data: dict, sid: str) -> dict:
    return data["people"].setdefault(sid, {"events": [], "last_seen": 0, "seen_days": []})


def observe(data: dict, sid: str, text: str, in_group: bool) -> None:
    """记一笔：这一轮谁说了什么（只写，不读）。"""
    if not sid:
        return
    now = _now()
    day = now // 86400
    person = _person(data, sid)

    person["last_seen"] = now
    days = person.setdefault("seen_days", [])
    if day not in days:
        days.append(day)
        person["seen_days"] = days[-400:]

    events = person.setdefault("events", [])

    # 亲密：每条消息一分，长消息和私聊更重
    weight = 1.0
    if len(text or "") >= 20:
        weight += 0.5
    if not in_group:
        weight += 1.0
    events.append({"kind": "closeness", "w": weight, "at": now})

    # 危险
    for pattern, w in DANGER_PATTERNS:
        if pattern.search(text or ""):
            events.append({"kind": "danger", "w": w, "at": now})
            break

    person["events"] = events[-600:]


def add_danger(data: dict, sid: str, w: float = 1.0, note: str = "") -> None:
    """外部判定器可以直接记一笔危险（如冒充），不用重新扫文本。"""
    if not sid:
        return
    person = _person(data, sid)
    person.setdefault("events", []).append(
        {"kind": "danger", "w": float(w), "at": _now(), "note": note[:80]}
    )
    person["events"] = person["events"][-600:]


def closeness(data: dict, sid: str, now: int = None) -> float:
    now = now or _now()
    person = data.get("people", {}).get(sid)
    if not person:
        return 0.0
    total = 0.0
    for e in person.get("events", []):
        if e.get("kind") != "closeness":
            continue
        total += float(e.get("w") or 1.0) * _decay(int(e.get("at") or now), now, CLOSENESS_HALF_LIFE_DAYS)
    return total


def danger(data: dict, sid: str, now: int = None) -> float:
    now = now or _now()
    person = data.get("people", {}).get(sid)
    if not person:
        return 0.0
    total = 0.0
    for e in person.get("events", []):
        if e.get("kind") != "danger":
            continue
        total += float(e.get("w") or 1.0) * _decay(int(e.get("at") or now), now, DANGER_HALF_LIFE_DAYS)
    return total


def presence(data: dict, sid: str, now: int = None) -> float:
    """0~1：多久没见。刚见过≈1，一周没见≈0.5，三周≈0.06"""
    now = now or _now()
    person = data.get("people", {}).get(sid)
    if not person:
        return 0.0
    last = int(person.get("last_seen") or 0)
    if not last:
        return 0.0
    return _decay(last, now, PRESENCE_HALF_LIFE_DAYS)


def summary(data: dict, sid: str, now: int = None) -> dict:
    return {
        "closeness": round(closeness(data, sid, now), 2),
        "danger": round(danger(data, sid, now), 2),
        "presence": round(presence(data, sid, now), 2),
    }
