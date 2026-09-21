from __future__ import annotations

import json


def strip_markdown_json_fence(text: str) -> str:
    """
    功能：移除模型输出外层可能包裹的 Markdown JSON 代码块。

    参数：
        text (str): 模型返回的原始文本。

    返回：
        str: 去掉代码块标记后的候选 JSON 文本。
    """
    cleaned_text = text.strip()
    if not cleaned_text.startswith("```"):
        return cleaned_text

    lines = cleaned_text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def extract_json_candidate(text: str) -> str:
    """
    功能：从模型解释性文本中截取最可能的 JSON 对象或数组片段。

    参数：
        text (str): 已初步清理的模型输出文本。

    返回：
        str: 候选 JSON 片段；若无法定位则返回原文本。
    """
    object_start = text.find("{")
    array_start = text.find("[")
    candidate_starts = [index for index in [object_start, array_start] if index != -1]
    if not candidate_starts:
        return text

    start_index = min(candidate_starts)
    end_object = text.rfind("}")
    end_array = text.rfind("]")
    end_index = max(end_object, end_array)
    if end_index <= start_index:
        return text

    # 很多模型会在 JSON 前后追加说明文字；截取外层括号能显著降低解析失败率。
    return text[start_index : end_index + 1].strip()


def escape_invalid_json_control_characters(text: str) -> str:
    """
    功能：修复 JSON 字符串内部直接出现的换行、回车、制表符等控制字符。

    参数：
        text (str): 候选 JSON 文本。

    返回：
        str: 控制字符已转义的 JSON 文本。
    """
    repaired_characters: list[str] = []
    inside_string = False
    escaped = False

    for current_character in text:
        if inside_string:
            if escaped:
                repaired_characters.append(current_character)
                escaped = False
                continue

            if current_character == "\\":
                repaired_characters.append(current_character)
                escaped = True
                continue

            if current_character == '"':
                repaired_characters.append(current_character)
                inside_string = False
                continue

            if current_character == "\n":
                repaired_characters.append("\\n")
                continue
            if current_character == "\r":
                repaired_characters.append("\\r")
                continue
            if current_character == "\t":
                repaired_characters.append("\\t")
                continue

            repaired_characters.append(current_character)
            continue

        repaired_characters.append(current_character)
        if current_character == '"':
            inside_string = True
            escaped = False

    return "".join(repaired_characters)


def escape_invalid_json_backslashes(text: str) -> str:
    """
    功能：修复 JSON 字符串中常见的 LaTeX 单反斜杠问题。

    参数：
        text (str): 候选 JSON 文本。

    返回：
        str: 无效反斜杠已转义的 JSON 文本。
    """
    repaired_characters: list[str] = []
    index = 0
    inside_string = False
    valid_escape_characters = {'"', "\\", "/", "b", "f", "n", "r", "t"}

    while index < len(text):
        current_character = text[index]

        if current_character == '"':
            inside_string = not inside_string
            repaired_characters.append(current_character)
            index += 1
            continue

        if current_character == "\\" and inside_string:
            next_character = text[index + 1] if index + 1 < len(text) else ""
            if next_character in valid_escape_characters:
                repaired_characters.append(current_character + next_character)
                index += 2
                continue

            if next_character == "u" and index + 5 < len(text):
                unicode_candidate = text[index + 2 : index + 6]
                if all(character in "0123456789abcdefABCDEF" for character in unicode_candidate):
                    repaired_characters.append(text[index : index + 6])
                    index += 6
                    continue

            repaired_characters.append("\\\\")
            index += 1
            continue

        repaired_characters.append(current_character)
        index += 1

    return "".join(repaired_characters)


def normalize_json_text(json_text: str) -> str:
    """
    功能：把模型输出清理并规范化为合法 JSON 字符串。

    参数：
        json_text (str): 模型返回的原始文本。

    返回：
        str: 已通过 JSON 校验、可直接保存的规范化 JSON 文本。
    """
    cleaned_text = strip_markdown_json_fence(json_text)
    extracted_text = extract_json_candidate(cleaned_text)
    control_repaired_text = escape_invalid_json_control_characters(extracted_text)
    backslash_repaired_text = escape_invalid_json_backslashes(extracted_text)
    fully_repaired_text = escape_invalid_json_backslashes(control_repaired_text)

    candidate_texts = [
        cleaned_text,
        extracted_text,
        control_repaired_text,
        backslash_repaired_text,
        fully_repaired_text,
    ]

    parse_errors: list[json.JSONDecodeError] = []
    for candidate_text in dict.fromkeys(candidate_texts):
        try:
            json_data = json.loads(candidate_text)
            return json.dumps(json_data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as error:
            parse_errors.append(error)

    preview_text = json_text[:300].replace("\n", "\\n")
    latest_error = parse_errors[-1]
    raise ValueError(f"模型输出不是合法 JSON：{latest_error}；输出预览：{preview_text}") from latest_error


def parse_model_json(json_text: str) -> dict:
    """
    功能：把模型输出解析为字典，统一供 provider 返回结构化结果。

    参数：
        json_text (str): 模型返回的原始文本。

    返回：
        dict: 解析后的 JSON 对象。
    """
    normalized_text = normalize_json_text(json_text)
    parsed_value = json.loads(normalized_text)
    if not isinstance(parsed_value, dict):
        raise ValueError("模型输出的顶层 JSON 必须是对象")
    return parsed_value
