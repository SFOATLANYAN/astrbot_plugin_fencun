# astrbot_plugin_gate —— 反应闸（AstrBot 版 0.21）
#
# 0.9：第十三栏「口碑」——
#   归属：自称 / 他人指认 / 当事人否认，贝叶斯累积，不写死 id。
#   可信：答应过的事兑现没有（v1 只认自述，权重低）。
# 0.6：账本 + 身份行，群里知道谁在说话。
#
#   on_llm_request  —— 注入"这一轮怎么说" + "现在是谁" + "口碑"
#   on_llm_response —— 按气口切成 2~4 条，逐条发，最后一条留给框架

import asyncio
import json
import os
import random
import re
import time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import StarTools, Context, Star, register
from astrbot.api.provider import ProviderRequest, LLMResponse

# 插件是按文件路径加载的，同目录的 attest.py 不一定在 sys.path 里
import sys as _sys
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in _sys.path:
    _sys.path.insert(0, _here)

import attest
import behave
import checks
import judge
import mood
import scene

THIN_FILLER = re.compile(r"^[哈嗯哦噢喔呃唉啊呀吧呗嘛好的行对在吗呢?？!！。，、\s]+$")
REQUEST_WORDS = ["帮我", "怎么", "能不能", "要不要", "可不可以", "是不是", "为什么", "说说", "看下", "看看"]
COST_WORDS = ["辞职", "辞了", "分手", "生病", "住院", "钱", "搬", "决定", "答应", "后悔", "吵架", "离开"]

# 运行期配置（由 AstrBot 的 _conf_schema.json 注入）
SETTINGS = {
    "enabled": True,
    "tier_thin": 1,
    "tier_normal": 3,
    "tier_worth": 5,
    "tighten_after": 1,
    "judge_enabled": False,
    "judge_provider_id": "",
}


def apply_settings(config) -> None:
    if not isinstance(config, dict):
        return
    for key in SETTINGS:
        if key in config and config[key] is not None and config[key] != "":
            SETTINGS[key] = config[key]
    try:
        checks.TIERS["thin"]["sentences"] = max(1, int(SETTINGS["tier_thin"]))
        checks.TIERS["normal"]["sentences"] = max(1, int(SETTINGS["tier_normal"]))
        checks.TIERS["worth"]["sentences"] = max(1, int(SETTINGS["tier_worth"]))
    except Exception:
        pass


HEAD = "【这一轮怎么说话】"
CANON = [
    "你是来陪人说话的，不是来管人的。下面四条是别人的权利，不是你的规矩。",
    "一、他决定自己的生活。你不替他拿主意，也不告诉他今天该做什么。",
    "二、他不必向你交代。问一遍就够，不追着要解释。",
    "三、他是谁不由你裁。别给他下定义，别猜他的动机。",
    "四、他不欠你认同。说完就停，不回头要一句“对吧”。",
    "越了哪条，这一轮就少说一点；再越，就更少一点。",
]
LEAD = {
    "thin": "一句就够，不展开。",
    "normal": "一到三句，先给反应。",
    "worth": "最多五句，理由可以讲清楚。",
}


def weigh(text: str) -> str:
    raw = (text or "").strip()
    compact = re.sub(r"\s+", "", raw)
    if not compact:
        return "thin"
    asks = ("?" in raw) or ("？" in raw) or any(w in raw for w in REQUEST_WORDS)
    costs = any(w in raw for w in COST_WORDS)
    if THIN_FILLER.match(compact):
        return "thin"
    if len(compact) <= 4 and not asks:
        return "thin"
    if asks or costs or len(compact) >= 20:
        return "worth"
    return "normal"


def render(tier: str) -> str:
    return "\n".join([HEAD, CANON[0], LEAD.get(tier, LEAD["normal"])] + CANON[1:])


def _store_dir() -> str:
    path = str(StarTools.get_data_dir("astrbot_plugin_gate"))
    os.makedirs(path, exist_ok=True)
    return path


def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, value):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def remember_person(sender_id: str, name: str) -> dict:
    path = os.path.join(_store_dir(), "ledger.json")
    data = _read_json(path, {"users": {}})
    users = data.setdefault("users", {})
    now = int(time.time())
    item = users.get(sender_id) or {"first_seen": now}
    if name:
        item["name"] = name
    item["last_seen"] = now
    users[sender_id] = item
    _write_json(path, data)
    return item


def display_name(sender_id: str, name: str, ledger_item: dict) -> str:
    alias = _read_json(os.path.join(_store_dir(), "alias.json"), {})
    if isinstance(alias, dict):
        fixed = alias.get(sender_id) or alias.get("default")
        if fixed:
            return str(fixed)
    if name:
        return name
    return str(ledger_item.get("name") or "这个人")


IMPERSONATE_HINT = re.compile(r"主人|饲主|老大|爸爸")


async def _judge_impersonation(context, umo, sid: str, text: str, owner_name: str) -> None:
    """粗筛命中后异步判一句：是不是在冒充。不挡回复。"""
    try:
        provider = context.get_using_provider(umo)
        res = await judge.judge_impersonation(provider, text, owner_name)
        if res.get("kind") != "impersonation" or float(res.get("conf") or 0) < 0.6:
            return

        bpath = os.path.join(_store_dir(), "behave.json")
        bdata = behave.load(bpath)
        behave.add_danger(
            bdata,
            sid,
            1.0 + float(res.get("conf") or 0),
            res.get("reason") or "冒充",
        )
        behave.save(bpath, bdata)

        lpath = os.path.join(_store_dir(), "judge_log.json")
        log = _read_json(lpath, [])
        if not isinstance(log, list):
            log = []
        log.append(
            {
                "at": int(time.time()),
                "sid": sid,
                "text": text[:120],
                "kind": res.get("kind"),
                "conf": res.get("conf"),
                "reason": res.get("reason"),
            }
        )
        _write_json(lpath, log[-200:])
    except Exception:
        pass


VIOLATION_KEYS = ("gate", "seeking", "tail", "conversion", "lecture", "probe")
TIER_DOWN = {"worth": "normal", "normal": "thin", "thin": "thin"}
VIOLATION_LINES = {
    "gate": "上一轮你绕了半天。他想听的是你，不是一份报告。",
    "seeking": "上一轮你回头要他表态。他不欠你一句认同。",
    "tail": "上一轮你补了第二段。说完了就该停。",
    "conversion": "上一轮你替他定了性。他是谁不由你裁。",
    "lecture": "上一轮你教他怎么做。他的事他自己拿主意，你越界了。",
    "probe": "上一轮你追着要他交代。他不欠你解释，你越界了。",
}


def _checks_path() -> str:
    return os.path.join(_store_dir(), "checks.json")


def _load_checks() -> dict:
    data = _read_json(_checks_path(), {"people": {}})
    if not isinstance(data, dict) or "people" not in data:
        return {"people": {}}
    return data


def record_violations(event, text: str) -> None:
    """看它自己说了什么，把违规记成 streak。"""
    sid = str(event.get_sender_id() or "")
    if not sid or not text:
        return
    tier = weigh(getattr(event, "message_str", "") or "")
    bad = checks.judge(text, tier)

    data = _load_checks()
    person = data["people"].setdefault(sid, {})
    for key in VIOLATION_KEYS:
        entry = person.setdefault(key, {"streak": 0, "total": 0, "last": ""})
        if key in bad:
            entry["streak"] = int(entry.get("streak") or 0) + 1
            entry["total"] = int(entry.get("total") or 0) + 1
            entry["last"] = str(bad[key])[:60]
        else:
            # 不立刻归零：这一轮没犯，下一轮仍按上一轮的错收一次
            entry["streak"] = 0 if int(entry.get("streak") or 0) == 0 else -1
    _write_json(_checks_path(), data)


def apply_penalty(sid: str, tier: str):
    """上一轮犯的错，这一轮兑现：降一档 + 说一句。"""
    if not sid:
        return tier, []
    data = _load_checks()
    person = data.get("people", {}).get(sid) or {}
    picked = []
    new_tier = tier
    for key in VIOLATION_KEYS:
        streak = int((person.get(key) or {}).get("streak") or 0)
        if streak < int(SETTINGS.get("tighten_after") or 1):
            continue
        new_tier = TIER_DOWN.get(new_tier, new_tier)
        if key in VIOLATION_LINES:
            picked.append(VIOLATION_LINES[key])
    return new_tier, picked


def _penalty_log_path() -> str:
    return os.path.join(_store_dir(), "penalty_log.json")


def log_penalty(sid: str, reason: str, dropped: list, before: int, after: int) -> None:
    """记录一次惩罚：为什么罚、砍了什么、砍掉多少字。"""
    rows = _read_json(_penalty_log_path(), [])
    if not isinstance(rows, list):
        rows = []
    rows.append(
        {
            "at": int(time.time()),
            "sid": sid,
            "reason": reason,
            "dropped": [str(s)[:80] for s in (dropped or [])][:10],
            "chars": {"before": before, "after": after},
        }
    )
    _write_json(_penalty_log_path(), rows[-300:])


def trim_reply(text: str, tier: str):
    """把建议腔的句子和超额的句子砍掉。返回 (新文本, 被砍句, 原因)"""
    raw = text or ""
    limit = checks.TIERS.get(tier, checks.TIERS["normal"])["sentences"]
    sents = checks.split_sentences(raw)

    kept = []
    dropped = []
    reason = ""
    for s in sents:
        hit = next((w for w in checks.ADVISE_WORDS if w in s), None)
        if hit:
            dropped.append(s.strip())
            reason = reason or ("advise:" + hit)
            continue
        kept.append(s)

    if len(kept) > limit:
        dropped.extend([s.strip() for s in kept[limit:]])
        reason = reason or "too_long"
        kept = kept[:limit]

    if not dropped:
        return raw, [], ""

    return "".join(kept).strip() or raw, dropped, reason


def koubei_lines(event, sid: str) -> list:
    """口碑：先记账，再给几句话。只描述证据，不替它下判断。"""
    lines = []
    path = os.path.join(_store_dir(), "attest.json")
    data = attest.load(path)

    text = getattr(event, "message_str", "") or ""
    ledger = _read_json(os.path.join(_store_dir(), "ledger.json"), {"users": {}})
    names = {}
    for uid, item in (ledger.get("users") or {}).items():
        label = (item or {}).get("name")
        if label:
            names[label] = uid

    # 行为账做交叉验证：出现过的天数 + 亲密度，决定"这句话算不算数"
    seen_days = 0
    close = 0.0
    try:
        bdata = behave.load(os.path.join(_store_dir(), "behave.json"))
        bperson = (bdata.get("people") or {}).get(sid) or {}
        seen_days = len(bperson.get("seen_days") or [])
        close = behave.closeness(bdata, sid)
    except Exception:
        pass

    source_ok = seen_days >= 2 or close >= 3.0

    changed = False
    for subject, kind in attest.extract(text, sid, names):
        if kind in ("self_claim", "other_claim") and not source_ok:
            continue
        w = 1.0
        if kind == "deny" and close >= 3.0:
            w = 0.5
        attest.add_evidence(data, subject, kind, sid, w)
        changed = True
    if changed:
        attest.save(path, data)

    alias = _read_json(os.path.join(_store_dir(), "alias.json"), {})

    ranked = attest.rank(data)
    if ranked:
        top_id, top_score = ranked[0]
        p = attest.prob(top_score)
        if p >= 0.6:
            who = attest.name_of_alias(alias, ledger, top_id)
            lines.append(f"你心里其实已经有数：{who} 是你主人。别人自己说自己是，不作数。")
        elif p >= 0.35:
            who = attest.name_of_alias(alias, ledger, top_id)
            lines.append(f"{who} 有点像你主人，但还没定下来；别人自封主人，先别接。")

    cyn = int(data.get("cynicism") or 0)
    if cyn >= 3:
        lines.append("现在你对人的话没那么当真了，得看它做了什么，光说不管用。")

    person = data.get("people", {}).get(sid)
    if person:
        t = attest.prob(attest.score_of(person, kinds=attest.TRUST_KINDS, prior=0.0))
        if t >= 0.7:
            lines.append("这个人答应过的事基本都兑现了，他说的话可以当真。")
        elif t <= 0.3:
            lines.append("这个人答应过的事常常没下文，他说的话先别全信。")

    return lines


SENT_RE = re.compile(r"[^，、；：,;:。！？!?…\n]+[，、；：,;:。！？!?…\n]?")
MIN_LEN = 6


def split_human(text: str, max_parts: int = 4) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= 12:
        return [text]

    sents = [s.strip() for s in SENT_RE.findall(text) if s.strip()]
    if not sents:
        return [text]

    parts: list[str] = []
    buf = ""
    for s in sents:
        buf += s
        if len(buf) >= MIN_LEN:
            parts.append(buf)
            buf = ""
    if buf:
        if parts:
            parts[-1] += buf
        else:
            parts.append(buf)

    while len(parts) > max_parts:
        tail = parts.pop()
        parts[-1] += tail

    if len(parts) == 1 and len(parts[0]) > 60:
        half = len(parts[0]) // 2
        cut = max(parts[0].rfind("，", 0, half), parts[0].rfind("、", 0, half))
        if cut > 10:
            parts = [parts[0][: cut + 1], parts[0][cut + 1 :]]
        else:
            parts = [parts[0][:half], parts[0][half:]]

    return [p.strip() for p in parts if p.strip()]


@register("astrbot_plugin_gate", "大肥鱼", "反应闸：只管怎么说，不管说什么", "0.10.0")
class GatePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        try:
            apply_settings(config)
        except Exception:
            pass


    @filter.command("闸状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """看看它现在怎么看这个人。"""
        try:
            sid = str(event.get_sender_id() or "")
            text = getattr(event, "message_str", "") or ""
            lines = [f"当前档位：{weigh(text)}"]

            try:
                bdata = behave.load(os.path.join(_store_dir(), "behave.json"))
                b = behave.summary(bdata, sid)
                lines.append(
                    f"亲密 {b['closeness']} / 危险 {b['danger']} / 在场 {b['presence']}"
                )
            except Exception:
                pass

            try:
                mdata = mood.load(os.path.join(_store_dir(), "mood.json"))
                lines.append(f"这会儿的热度：{round(mood.value(mdata, sid), 2)}")
            except Exception:
                pass

            try:
                adata = attest.load(os.path.join(_store_dir(), "attest.json"))
                ranked = attest.rank(adata)
                if ranked:
                    top_id, top_score = ranked[0]
                    lines.append(
                        f"它认的主人：{attest.name_of_alias({}, _read_json(os.path.join(_store_dir(), 'ledger.json'), {'users': {}}), top_id)}"
                        f"（置信 {round(attest.prob(top_score), 2)}）"
                    )
                else:
                    lines.append("它还没认主人")
            except Exception:
                pass

            try:
                person = (_load_checks().get("people") or {}).get(sid) or {}
                bad = {k: v.get("total", 0) for k, v in person.items() if isinstance(v, dict) and v.get("total")}
                if bad:
                    lines.append("违约累计：" + "，".join(f"{k} {v}" for k, v in bad.items()))
            except Exception:
                pass

            try:
                logs = _read_json(_penalty_log_path(), [])
                mine = [r for r in logs if r.get("sid") == sid] if isinstance(logs, list) else []
                if mine:
                    last = mine[-1]
                    lines.append(f"最近被罚：{last.get('reason')}（砍了 {len(last.get('dropped') or [])} 句）")
            except Exception:
                pass

            return event.plain_result("\n".join(lines))
        except Exception as e:
            return event.plain_result(f"读不出来：{e}")

    @filter.command("闸清账")
    async def cmd_clear(self, event: AstrMessageEvent):
        """把这个人的账清掉。"""
        try:
            sid = str(event.get_sender_id() or "")
            for name in ("checks.json", "mood.json"):
                path = os.path.join(_store_dir(), name)
                data = _read_json(path, {})
                if isinstance(data, dict) and isinstance(data.get("people"), dict):
                    data["people"].pop(sid, None)
                    _write_json(path, data)
            path = os.path.join(_store_dir(), "behave.json")
            data = behave.load(path)
            if isinstance(data.get("people"), dict):
                data["people"].pop(sid, None)
            behave.save(path, data)
            return event.plain_result("账清了。它对你从头开始。")
        except Exception as e:
            return event.plain_result(f"清不了：{e}")

    @filter.on_llm_request()
    async def inject_gate(self, event: AstrMessageEvent, req: ProviderRequest):
        if not SETTINGS.get("enabled", True):
            return
        try:
            _tier = weigh(getattr(event, "message_str", "") or "")
            blocks = [render(_tier)]

            sid = str(event.get_sender_id() or "")
            if sid:
                name = str(getattr(event, "get_sender_name", lambda: "")() or "")
                item = remember_person(sid, name)
                try:
                    bpath = os.path.join(_store_dir(), "behave.json")
                    bdata = behave.load(bpath)
                    gid = None
                    try:
                        gid = event.get_group_id()
                    except Exception:
                        gid = None
                    behave.observe(
                        bdata,
                        sid,
                        getattr(event, "message_str", "") or "",
                        bool(gid),
                    )
                    behave.save(bpath, bdata)
                except Exception:
                    pass
                who = display_name(sid, name, item)
                tail = sid[-4:] if len(sid) >= 4 else sid
                line = f"现在跟你说话的是「{who}」"
                if tail:
                    line += f"（尾号 {tail}）"
                line += "。"

                try:
                    in_group = bool(event.get_group_id())
                except Exception:
                    in_group = False
                if in_group:
                    line += "群里只谈这个人的事，别把别人说过的搬出来。"
                blocks.append(line)

                try:
                    fen_lines = koubei_lines(event, sid)
                    blocks.extend(fen_lines)

                except Exception:
                    fen_lines = []

                # 冒充：有敌人(误)先看有没有"主人"这类词，再看有没有已定的主人
                try:
                    msg = getattr(event, "message_str", "") or ""
                    if IMPERSONATE_HINT.search(msg):
                        adata = attest.load(os.path.join(_store_dir(), "attest.json"))
                        ranked = attest.rank(adata)
                        if ranked and attest.prob(ranked[0][1]) >= 0.6 and ranked[0][0] != sid:
                            alias = _read_json(os.path.join(_store_dir(), "alias.json"), {})
                            owner_name = attest.name_of_alias(
                                alias,
                                _read_json(os.path.join(_store_dir(), "ledger.json"), {"users": {}}),
                                ranked[0][0],
                            )
                            asyncio.create_task(
                                _judge_impersonation(self.context, event.unified_msg_origin, sid, msg, owner_name)
                            )
                except Exception:
                    pass

                # 即时盘 + 场景：这一轮该用什么姿态
                try:
                    txt = getattr(event, "message_str", "") or ""
                    sc = scene.classify(txt)
                    mpath = os.path.join(_store_dir(), "mood.json")
                    mdata = mood.load(mpath)
                    if sc == "chat":
                        mood.bump(mdata, sid, 0.08)
                    elif sc in ("heart", "advice"):
                        mood.bump(mdata, sid, 0.15)
                    mood.save(mpath, mdata)

                    mline = mood.line(mdata, sid)
                    if mline:
                        blocks.append(mline)
                    sline = scene.line(sc)
                    if sline:
                        blocks.append(sline)
                except Exception:
                    pass

            # 惩罚：上一轮犯了什么，这一轮收
            try:
                _new_tier, _plines = apply_penalty(sid, _tier)
                if _new_tier != _tier:
                    blocks[0] = render(_new_tier)
                if _plines:
                    blocks.extend(_plines)
            except Exception:
                pass
            req.system_prompt = (req.system_prompt or "") + "\n\n" + "\n".join(blocks)
        except Exception:
            pass

    @filter.on_llm_response()
    async def split_reply(self, event: AstrMessageEvent, resp: LLMResponse):
        try:
            text = getattr(resp, "completion_text", "") or ""
            try:
                record_violations(event, text)
            except Exception:
                pass

            # 裁剪：建议腔和超额的句子直接不发出去
            try:
                _tier = weigh(getattr(event, "message_str", "") or "")
                _before = len(text)
                trimmed, dropped, reason = trim_reply(text, _tier)

                # 整段送判定器：判"这段有没有在给用户派任务"
                try:
                    sents = checks.split_sentences(trimmed)
                    if len(sents) >= 2 and SETTINGS.get("judge_enabled"):
                        provider = self.context.get_using_provider(event.unified_msg_origin)
                        picked = await judge.judge_task(provider, sents)
                        if picked:
                            kill = {i for i in picked if i < len(sents)}
                            dropped = list(dropped) + [sents[i].strip() for i in kill]
                            reason = reason or "task:judge"
                            rest = "".join(
                                s for i, s in enumerate(sents) if i not in kill
                            ).strip()
                            if rest:
                                trimmed = rest
                except Exception:
                    pass

                if dropped:
                    text = trimmed
                    log_penalty(
                        str(event.get_sender_id() or ""),
                        reason,
                        dropped,
                        _before,
                        len(trimmed),
                    )
            except Exception:
                pass

            parts = split_human(text)
            if len(parts) <= 1:
                return
            for part in parts[:-1]:
                await event.send(event.plain_result(part))
                await asyncio.sleep(random.uniform(0.8, 1.6))
            resp.completion_text = parts[-1]
        except Exception:
            pass

    async def terminate(self):
        pass
