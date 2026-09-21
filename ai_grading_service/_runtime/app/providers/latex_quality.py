from __future__ import annotations

import re


# 这些模式只覆盖确定性较高的公式特征，宁可把边界情况交给提示词，也不把普通英文误判为公式。
RAW_LATEX_COMMAND_PATTERN = re.compile(
    r"\\(?:frac|dfrac|tfrac|sum|prod|int|sqrt|lim|lambda|alpha|beta|gamma|theta|mu|pi|infty|"
    r"begin|end|left|right|geq|leq|neq|cdot|times|partial|nabla|mathrm|mathbf|operatorname)\b"
)
UNICODE_MATH_SYMBOL_PATTERN = re.compile(r"[∑∏∫√∞≠≤≥≈±×÷∂∇∈∉⊂⊆⊃⊇∪∩→⇒⇔]")
VARIABLE_EXPRESSION_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-zΑ-Ωα-ω](?:_[A-Za-z0-9]+|\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?:=|<=|>=|<|>)\s*(?:[A-Za-zΑ-Ωα-ω0-9({\\])"
)
POWER_OR_SUBSCRIPT_PATTERN = re.compile(r"(?<![\w])[A-Za-zΑ-Ωα-ω][A-Za-z0-9]*[_^](?:\{[^}]+\}|[A-Za-z0-9])")
NUMERIC_FRACTION_PATTERN = re.compile(r"(?<![\w])\d+\s*/\s*\d+(?![\w])")
FUNCTION_EXPRESSION_PATTERN = re.compile(r"(?<![\w])[A-Za-zΑ-Ωα-ω]\s*\([^)]*(?:\d+[A-Za-z]|[+*/^=])")


def find_text_latex_issues(text: str) -> list[str]:
    """
    功能：检查单个展示文本中的 LaTeX 定界符和明显裸公式特征。

    参数：
        text (str): 待检查的展示文本。

    返回：
        list[str]: 当前文本命中的问题类型；空列表表示未发现确定性问题。
    """
    outside_text, delimiter_balanced = extract_text_outside_latex(text)
    issues: list[str] = []
    if not delimiter_balanced:
        issues.append("LaTeX 定界符未闭合")

    # 只检查公式定界符外的文本，已经正确放在 $...$ 或 $$...$$ 中的内容不会误报。
    if RAW_LATEX_COMMAND_PATTERN.search(outside_text):
        issues.append("LaTeX 命令未放入公式定界符")
    if UNICODE_MATH_SYMBOL_PATTERN.search(outside_text):
        issues.append("数学符号未放入公式定界符")
    if VARIABLE_EXPRESSION_PATTERN.search(outside_text):
        issues.append("变量表达式未放入公式定界符")
    if POWER_OR_SUBSCRIPT_PATTERN.search(outside_text):
        issues.append("上下标表达式未放入公式定界符")
    if NUMERIC_FRACTION_PATTERN.search(outside_text):
        issues.append("分式未放入公式定界符")
    if FUNCTION_EXPRESSION_PATTERN.search(outside_text):
        issues.append("函数表达式未放入公式定界符")

    return issues


def extract_text_outside_latex(text: str) -> tuple[str, bool]:
    """
    功能：移除已由 LaTeX 定界符包裹的内容，保留需要继续检查的普通文本。

    参数：
        text (str): 可能同时包含自然语言和 LaTeX 公式的文本。

    返回：
        tuple[str, bool]: 公式外文本，以及所有公式定界符是否正确闭合。
    """
    outside_characters: list[str] = []
    active_delimiter = ""
    index = 0

    while index < len(text):
        # 转义美元符号不是公式边界，应作为普通文本保留。
        if text[index] == "\\" and index + 1 < len(text) and text[index + 1] == "$":
            if not active_delimiter:
                outside_characters.append("$")
            index += 2
            continue

        if not active_delimiter:
            if text.startswith("$$", index):
                active_delimiter = "$$"
                index += 2
                continue
            if text[index] == "$":
                active_delimiter = "$"
                index += 1
                continue

            outside_characters.append(text[index])
            index += 1
            continue

        if text.startswith(active_delimiter, index):
            index += len(active_delimiter)
            active_delimiter = ""
            # 用空格隔开公式两侧，避免移除公式后意外拼接出新的疑似表达式。
            outside_characters.append(" ")
            continue

        index += 1

    return "".join(outside_characters), not active_delimiter
