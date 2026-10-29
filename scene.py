# 场景分类：这句在问什么？决定两个盘谁说了算。
#
#   chat   —— 闲聊、陪嘴、情绪表达      → 即时盘 0.8
#   mix    —— 一般对话                  → 0.65
#   advice —— 求建议、问怎么办          → 基础盘 0.6（应对方式派用场）
#   heart  —— 心事、讲关系、讲感受      → 基础盘 0.55
#
# 判据只用"看得见的信号"，不用"我觉得"。

import re

ADVICE = re.compile(r"我该|该不该|怎么办|怎么回|怎么聊|要不要|你觉得我|怎么才能|有没有什么办法")
HEART = re.compile(r"我(喜欢|爱|讨厌|难过|开心|害怕|后悔|焦虑)|分手|吵架|他\s*这个人|她\s*这个人|我们俩|谈恋爱")
QUESTION_START = re.compile(r"^(那|所以|可是|但是)?(怎么|为什么|为什么说|凭什么)")

WEIGHTS = {
    "chat": {"now": 0.8, "base": 0.2},
    "mix": {"now": 0.65, "base": 0.35},
    "advice": {"now": 0.4, "base": 0.6},
    "heart": {"now": 0.45, "base": 0.55},
}


def classify(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "chat"
    if HEART.search(raw):
        return "heart"
    if ADVICE.search(raw):
        return "advice"
    if QUESTION_START.search(raw) and len(raw) >= 10:
        return "advice"
    if len(raw) <= 8:
        return "chat"
    return "mix"


def weights(scene: str) -> dict:
    return WEIGHTS.get(scene, WEIGHTS["mix"])


def line(scene: str) -> str:
    """翻成人话：只在需要换姿态的时候说一句。"""
    if scene == "heart":
        return "他在跟你说心里的事，先接住这件事本身，别急着给办法。"
    if scene == "advice":
        return "他在问怎么办，先站在他那边看一眼，再给一个今天就能做的动作。"
    return ""
