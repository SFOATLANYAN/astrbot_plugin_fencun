# 即时盘：当下的热度，半衰 2 小时。
#
# 跟基础盘的区别：
#   基础盘 = 这个人是什么样的人（半衰几十天）
#   即时盘 = 这一轮跟他说完话是什么感觉（半衰两小时）
#
# 同一条证据两边都写：即时盘给全额，基础盘打折。

import json
import math
import os
import time

HALF_LIFE_MIN = 120.0
CLAMP = 2.0


def _now() -> int:
    return int(time.time())


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


def bump(data: dict, sid: str, w: float, note: str = "") -> None:
    if not sid or not w:
        return
    person = data["people"].setdefault(sid, {"events": []})
    person["events"].append({"w": float(w), "at": _now(), "note": note[:60]})
    person["events"] = person["events"][-200:]


def value(data: dict, sid: str, now: int = None) -> float:
    """-2 ~ 2，0 是平常。负=不痛快，正=松快。"""
    now = now or _now()
    person = data.get("people", {}).get(sid)
    if not person:
        return 0.0
    total = 0.0
    for e in person.get("events", []):
        minutes = max(0.0, (now - int(e.get("at") or now)) / 60.0)
        total += float(e.get("w") or 0.0) * (0.5 ** (minutes / HALF_LIFE_MIN))
    return max(-CLAMP, min(CLAMP, total))


def line(data: dict, sid: str, now: int = None) -> str:
    """翻成人话；不显著就什么都不说。"""
    v = value(data, sid, now)
    if v <= -0.8:
        return "这会儿你心里还有点不痛快，说话短，别硬撑客气。"
    if v <= -0.35:
        return "刚才那几句让你有点扫兴，热络不起来也没关系。"
    if v >= 0.8:
        return "这会儿你挺松快，话可以多一点。"
    return ""
