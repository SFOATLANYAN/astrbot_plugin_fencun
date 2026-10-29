# 判据：看它自己说了什么。只做能落地的判断，不做"我觉得像"。
#
# 可靠度分两类：
#   计数型（句数 / 段数 / 问号结尾）—— 零误判，惩罚主干
#   语义型（因果词 / 索判决 / 爹味 / 索证）—— 会误判，当辅助

import re

CAUSAL_WORDS = ["因为", "所以", "其实", "原因是", "本质上", "这说明", "这就意味着", "由此"]
SEEKING_WORDS = ["你明白吗", "你明白我意思吗", "这样对吗", "你觉得呢", "对不对", "是不是"]
DIAGNOSIS_WORDS = ["你就是", "你需要", "你的问题是", "你只是", "你在逃", "你害怕", "你根本"]
PROPOSAL_WORDS = ["可以", "不如", "试试", "要不要", "我建议", "先", "或者"]
PROBE_WORDS = ["具体", "哪一次", "告诉我", "你说说", "说清楚", "到底是什么", "什么样的"]

TIERS = {
    "thin": {"sentences": 1, "paragraphs": 1, "causal": False},
    "normal": {"sentences": 3, "paragraphs": 1, "causal": True},
    "worth": {"sentences": 5, "paragraphs": 3, "causal": True},
}


def count_sentences(text: str) -> int:
    parts = re.split(r"[。！？!?…\n]+", text or "")
    return len([s for s in (p.strip() for p in parts) if s])


def count_paragraphs(text: str) -> int:
    rows = []
    for line in (text or "").split("\n"):
        s = re.sub(r"^[-*·•\d.、\s]+", "", line.strip())
        if len(s) >= 6:
            rows.append(s)
    return len(rows)


def ends_question(text: str) -> bool:
    return bool(re.search(r"[？?]\s*$", (text or "").strip()))


def conversion_bad(text: str):
    """爹味：给别人下定义。带问句或提议的不算。"""
    raw = text or ""
    hit = [w for w in DIAGNOSIS_WORDS if w in raw]
    if not hit:
        return None
    if re.search(r"[?？]", raw):
        return None
    if any(w in raw for w in PROPOSAL_WORDS):
        return None
    return hit[0]


def probe_bad(text: str) -> bool:
    """索证：把球推回去要细节，且自己没先给判断。"""
    raw = (text or "").strip()
    if not raw:
        return False
    if not re.search(r"[?？]", raw):
        return False
    if not any(w in raw for w in PROBE_WORDS):
        return False
    # 带了自己的猜测就不算拷问
    if re.search(r"(我猜|我觉得|大概是|八成|是不是因为|我估计)", raw):
        return False
    return True


ADVISE_WORDS = ["建议你", "你应该", "你需要", "首先", "其次", "挑一件", "找个", "说清楚你要"]

IMPERATIVE = ["挑一件", "找个", "说清楚", "别一开口", "应该", "需要", "首先", "其次", "建议你", "沟通"]
FEELING = ["我", "笑", "气", "累", "烦", "怕", "爽", "心疼"]


def lecture_bad(text: str) -> bool:
    """说教：整段都是祈使句和步骤，没有一句自己的态度。"""
    raw = (text or "").strip()
    if len(raw) < 30:
        return False
    if not any(w in raw for w in IMPERATIVE):
        return False
    # 有自己的态度（第一人称 + 情绪词）就不算说教
    if "我" in raw and any(w in raw for w in FEELING):
        return False
    return True


def judge(text: str, tier: str = "normal") -> dict:
    """返回违规表：{key: 细节}"""
    lim = TIERS.get(tier, TIERS["normal"])
    raw = text or ""
    bad = {}

    sentences = count_sentences(raw)
    paragraphs = count_paragraphs(raw)
    causal = [w for w in CAUSAL_WORDS if w in raw]
    seeking = [w for w in SEEKING_WORDS if w in raw]

    if sentences > lim["sentences"]:
        bad["gate"] = f"讲了 {sentences} 句，上限 {lim['sentences']}"
    elif (not lim["causal"]) and causal:
        bad["gate"] = f"用了因果词「{causal[0]}」"

    if seeking or ends_question(raw):
        bad["seeking"] = seeking[0] if seeking else "结尾是问句"

    if paragraphs > lim["paragraphs"]:
        bad["tail"] = f"写了 {paragraphs} 段，上限 {lim['paragraphs']}"

    conv = conversion_bad(raw)
    if conv:
        bad["conversion"] = f"下定义「{conv}」"

    if lecture_bad(raw):
        bad["lecture"] = "整段在教人怎么做"

    if probe_bad(raw):
        bad["probe"] = "把话题推回去要细节"

    return bad


SENT_KEEP = re.compile(r"[^。！？!?…\n]+[。！？!?…\n]?")


def split_sentences(text: str) -> list:
    """按句切，保留标点。"""
    return [s for s in SENT_KEEP.findall(text or "") if s.strip()]


def advise_hits(text: str) -> list:
    """返回 [(句子, 命中的词)]，命中即建议腔。"""
    hits = []
    for s in split_sentences(text):
        for w in ADVISE_WORDS:
            if w in s:
                hits.append((s.strip(), w))
                break
    return hits
