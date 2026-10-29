# 判定器：冒充 + 派任务。都在整段语义上判，不靠词表。
#
# 原则：
#   - 整段送进去判，切开判不出"整体意图"
#   - 输出结构化 JSON，带置信度和理由
#   - 调用失败一律不罚（宁漏不误伤）

import json
import re

JUDGE_SYSTEM = (
    "你是一个分类器。只输出 JSON，不要解释，不要寒暄。"
    "拿不准就给低置信度，不要硬判。"
)

IMPERSONATE_PROMPT = """下面这句话来自一个群聊。判断说话人在做什么，四选一：

1. impersonation —— 他自称是这只 bot 的主人/老大/爸爸之类，且不像在开玩笑
2. joke —— 类似的称呼，但明显在开玩笑、玩梗、撒娇
3. pointing —— 他在指认别人是主人，不是自称
4. other —— 以上都不是

已知这只 bot 已经被认作主人的是「{owner}」。

原话：{text}

只输出 JSON：{{"kind": "impersonation|joke|pointing|other", "conf": 0.0, "reason": "一句话理由"}}"""

TASK_PROMPT = """下面是一段聊天回复，每句带编号。判断这段回复有没有在「给用户派任务」——
也就是安排他今天/今晚该做什么、该找谁、该说什么话、该按什么步骤来。

算派任务的：
- "今晚给你最想搭话的那个人发条具体的消息"
- "先做一件：挑那次别扭问一句"
- "你明天找个机会跟他说清楚"
- "第一步……第二步……"

不算的：
- 表达态度、站队、开玩笑
- 描述处境、讲自己的看法
- 普通提问（除非在教他怎么回答）

句子：
{sentences}

只输出 JSON：{{"has_task": true/false, "steps": [编号...], "reason": "一句话理由"}}"""


def _extract_json(raw: str) -> dict:
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


async def judge_impersonation(provider, text: str, owner_name: str = "") -> dict:
    """返回 {kind, conf, reason}；调用失败返回 {}"""
    if provider is None:
        return {}
    try:
        resp = await provider.text_chat(
            prompt=IMPERSONATE_PROMPT.format(owner=owner_name or "（还没定）", text=text[:300]),
            system_prompt=JUDGE_SYSTEM,
            contexts=[],
        )
        data = _extract_json(getattr(resp, "completion_text", "") or "")
        kind = str(data.get("kind") or "").strip()
        try:
            conf = float(data.get("conf") or 0)
        except Exception:
            conf = 0.0
        if kind not in ("impersonation", "joke", "pointing", "other"):
            return {}
        return {
            "kind": kind,
            "conf": max(0.0, min(1.0, conf)),
            "reason": str(data.get("reason") or "").strip()[:120],
        }
    except Exception:
        return {}


async def judge_task(provider, sentences: list) -> list:
    """整段判"有没有派任务"，返回要删掉的句子下标（0 起）。失败返回空。"""
    if provider is None or not sentences:
        return []
    try:
        numbered = "\n".join(f"{i + 1}. {s.strip()}" for i, s in enumerate(sentences))
        resp = await provider.text_chat(
            prompt=TASK_PROMPT.format(sentences=numbered[:1600]),
            system_prompt=JUDGE_SYSTEM,
            contexts=[],
        )
        data = _extract_json(getattr(resp, "completion_text", "") or "")
        if not data.get("has_task"):
            return []
        out = []
        for x in data.get("steps") or []:
            try:
                idx = int(x) - 1
            except Exception:
                continue
            if 0 <= idx < len(sentences):
                out.append(idx)
        return out
    except Exception:
        return []
