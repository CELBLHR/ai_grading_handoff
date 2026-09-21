from __future__ import annotations

from typing import Any


QUESTION_TYPES = {"calculation", "proof", "discussion", "mixed", "fill_blank", "unknown"}

# 题型规范化会移除下划线、连字符和空格，因此不能直接用压缩后的文本查询原始枚举集合。
# 显式维护“压缩形式 -> 规范枚举”的映射，可以确保 fill_blank 这类带分隔符的枚举稳定还原。
COMPACT_QUESTION_TYPES = {
    question_type.replace("_", "").replace("-", "").replace(" ", ""): question_type
    for question_type in QUESTION_TYPES
}

FILL_BLANK_ALIASES = {
    "fillblank",
    "fillintheblank",
    "blank",
    "blanks",
    "填空",
    "填空题",
    "多空填空",
    "多空填空题",
    "计算填空",
    "计算填空题",
}

CALCULATION_ALIASES = {
    "calculate",
    "calculation",
    "computational",
    "计算",
    "计算题",
    "简单计算",
    "简单计算题",
    "综合计算",
    "综合计算题",
    "求值",
    "求值题",
    "求解",
    "求解题",
    "极限",
    "极限题",
    "求极限",
    "导数",
    "求导",
    "微分",
    "积分",
    "不定积分",
    "定积分",
    "广义积分",
    "方程",
    "级数",
    "级数题",
    "级数求和",
    "求和函数",
    "判断敛散",
    "判断敛散性",
    "敛散",
    "敛散性",
    "收敛性",
    "发散性",
    "判别敛散",
    "级数敛散",
    # Mojibake aliases kept for legacy drafts that were saved with the wrong code page.
    "璁＄畻",
    "璁＄畻棰",
    "绠€鍗曡绠楅",
    "缁煎悎璁＄畻棰",
    "鍒ゆ柇鏁涙暎鎬",
    "姹傚€",
}

PROOF_ALIASES = {
    "proof",
    "证明",
    "证明题",
    "论证",
    "论证题",
    "璇佹槑",
    "璇佹槑棰",
}

DISCUSSION_ALIASES = {
    "discussion",
    "讨论",
    "讨论题",
    "分类讨论",
    "参数讨论",
    "分段讨论",
    "分情况讨论",
    "开放讨论",
    "璁ㄨ",
    "鍒嗙被",
    "鍒嗘",
    "鍙傛暟",
}

MIXED_ALIASES = {
    "mixed",
    "综合",
    "综合题",
    "综合应用",
    "综合应用题",
    "应用题",
    "多问综合",
    "缁煎悎",
    "缁煎悎棰",
}


def normalize_question_type(raw_type: Any) -> str:
    """
    功能：把题目配置中的自由文本题型规范化为 V2 Rule Pack 枚举。

    参数：
        raw_type (Any): 导入文件、历史数据或教师编辑产生的原始题型值。

    返回：
        str: calculation、proof、discussion、mixed、fill_blank 或 unknown。
    """
    text = str(raw_type or "").strip().lower()
    if not text:
        return "unknown"

    compact = (
        text.replace("_", "")
        .replace("-", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("：", "")
        .replace(":", "")
    )

    canonical_type = COMPACT_QUESTION_TYPES.get(compact)
    if canonical_type:
        return canonical_type

    # 填空题描述中可能同时出现“计算”，必须先识别作答形式，避免“计算填空题”误用计算题 Rule Pack。
    if _matches_alias(compact, FILL_BLANK_ALIASES):
        return "fill_blank"

    # 计算题先于综合题判断，是为了让“综合计算题”继续使用计算题规则及最终答案保护策略。
    if _matches_alias(compact, CALCULATION_ALIASES):
        return "calculation"
    if _matches_alias(compact, PROOF_ALIASES):
        return "proof"
    if _matches_alias(compact, DISCUSSION_ALIASES):
        return "discussion"
    if _matches_alias(compact, MIXED_ALIASES):
        return "mixed"

    return "unknown"


def get_question_type(question: dict[str, Any]) -> str:
    """
    功能：从题目配置中读取并规范化已确认题型。

    参数：
        question (dict[str, Any]): 流水线题目对象，可能包含 rubric.type、question_type 或顶层 type。

    返回：
        str: 优先使用 rubric.type，其次使用显式 question_type 和顶层 type 得到的规范题型。
    """
    rubric = question.get("rubric") if isinstance(question.get("rubric"), dict) else {}
    return normalize_question_type(rubric.get("type") or question.get("question_type") or question.get("type"))


def _matches_alias(text: str, aliases: set[str]) -> bool:
    """
    功能：判断压缩后的题型文本是否包含任一兼容别名。

    参数：
        text (str): 已移除分隔符并转换为小写的题型文本。
        aliases (set[str]): 当前规范题型允许的兼容别名集合。

    返回：
        bool: 命中任一别名时返回 True，否则返回 False。
    """
    return any(alias in text for alias in aliases)
