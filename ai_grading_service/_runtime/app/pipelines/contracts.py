from __future__ import annotations

from typing import Any


REQUIRED_OUTPUT_FIELDS: dict[str, set[str]] = {
    "visual_ocr": {"transcription", "normalized_input", "ocr_risk"},
    "grading": {"suggested_score", "max_score", "score_points", "deductions", "summary"},
    "risk_gate": {"confidence_level", "review_advice", "risk_tags", "review_focus", "evidence_safe"},
    "evidence_risk": {
        "evidence_risk_level",
        "risk_tags",
        "risk_locations",
        "uncertain_symbols",
        "consistency_risks",
        "final_answer_risks",
        "grading_constraints",
    },
    "method_analysis": {"method_summary", "method_confidence", "method_evidence_blocks"},
    "scoring": {"suggested_score", "max_score", "score_points", "deductions", "summary"},
    "review_decision": {"confidence_level", "review_advice", "risk_tags", "review_focus", "evidence_safe"},
    "homework_review": {
        "answer_status",
        "summary",
        "strengths",
        "issues",
        "improvement_suggestions",
        "confidence_level",
        "review_advice",
        "risk_tags",
        "review_focus",
        "evidence_safe",
    },
    "homework_review_decision": {
        "confidence_level",
        "review_advice",
        "risk_tags",
        "review_focus",
        "evidence_safe",
    },
}

ALLOWED_CONFIDENCE_LEVELS = {"high", "medium", "low"}
ALLOWED_REVIEW_ADVICES = {"auto_accept", "sample_review", "required_review", "second_review"}
ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
ALLOWED_METHOD_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_HOMEWORK_ANSWER_STATUSES = {
    "correct",
    "partially_correct",
    "incorrect",
    "incomplete",
    "blank",
    "unclear",
}
ALLOWED_HOMEWORK_ISSUE_TYPES = {
    "conceptual",
    "calculation",
    "logic",
    "notation",
    "missing_step",
    "final_answer",
}


def validate_stage_output(stage_type: str, output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验并轻量规范化 stage 输出，保证后续结果表可以稳定消费。

    参数：
        stage_type (str): 当前阶段类型。
        output (dict[str, Any]): provider 返回的结构化输出。

    返回：
        dict[str, Any]: 通过校验后的输出对象。
    """
    if not isinstance(output, dict):
        raise ValueError(f"{stage_type} 输出必须是 JSON 对象")

    missing_fields = sorted(REQUIRED_OUTPUT_FIELDS.get(stage_type, set()) - set(output.keys()))
    if missing_fields:
        raise ValueError(f"{stage_type} 输出缺少必要字段：{', '.join(missing_fields)}")

    if stage_type in {"grading", "scoring"}:
        return validate_scoring_output(stage_type, output)
    if stage_type in {"risk_gate", "review_decision"}:
        return validate_review_decision_output(stage_type, output)
    if stage_type == "homework_review_decision":
        return validate_review_decision_output(stage_type, output)
    if stage_type == "visual_ocr":
        return validate_visual_output(output)
    if stage_type == "evidence_risk":
        return validate_evidence_risk_output(output)
    if stage_type == "method_analysis":
        return validate_method_analysis_output(output)
    if stage_type == "homework_review":
        return validate_homework_review_output(output)

    return output


def validate_visual_output(output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验视觉阶段输出的基础结构。

    参数：
        output (dict[str, Any]): 视觉阶段输出。

    返回：
        dict[str, Any]: 通过校验后的视觉输出。
    """
    if not isinstance(output.get("transcription"), dict):
        raise ValueError("visual_ocr.transcription 必须是对象")
    if not isinstance(output.get("normalized_input"), dict):
        raise ValueError("visual_ocr.normalized_input 必须是对象")
    if not isinstance(output.get("ocr_risk"), dict):
        raise ValueError("visual_ocr.ocr_risk 必须是对象")
    return output


def validate_grading_output(output: dict[str, Any]) -> dict[str, Any]:
    return validate_scoring_output("grading", output)


def validate_scoring_output(stage_type: str, output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验批改阶段输出，并把分数转换为浮点数。

    参数：
        output (dict[str, Any]): 批改阶段输出。

    返回：
        dict[str, Any]: 通过校验后的批改输出。
    """
    try:
        output["suggested_score"] = float(output["suggested_score"])
        output["max_score"] = float(output["max_score"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{stage_type}.suggested_score 和 {stage_type}.max_score 必须是数字") from exc

    if output["suggested_score"] < 0:
        raise ValueError(f"{stage_type}.suggested_score 不能小于 0")
    if output["max_score"] <= 0:
        raise ValueError(f"{stage_type}.max_score 必须大于 0")
    if output["suggested_score"] > output["max_score"]:
        raise ValueError(f"{stage_type}.suggested_score 不能大于 {stage_type}.max_score")
    if not isinstance(output["score_points"], list):
        raise ValueError(f"{stage_type}.score_points 必须是数组")
    if not isinstance(output["deductions"], list):
        raise ValueError(f"{stage_type}.deductions 必须是数组")
    if not isinstance(output["summary"], str):
        raise ValueError(f"{stage_type}.summary 必须是字符串")

    return output


def validate_risk_gate_output(output: dict[str, Any]) -> dict[str, Any]:
    return validate_review_decision_output("risk_gate", output)


def validate_review_decision_output(stage_type: str, output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验闸门阶段输出，确保处理建议和置信等级属于固定枚举。

    参数：
        output (dict[str, Any]): 闸门阶段输出。

    返回：
        dict[str, Any]: 通过校验后的闸门输出。
    """
    if output["confidence_level"] not in ALLOWED_CONFIDENCE_LEVELS:
        raise ValueError(f"{stage_type}.confidence_level 非法：{output['confidence_level']}")
    if output["review_advice"] not in ALLOWED_REVIEW_ADVICES:
        raise ValueError(f"{stage_type}.review_advice 非法：{output['review_advice']}")
    if not isinstance(output["risk_tags"], list):
        raise ValueError(f"{stage_type}.risk_tags 必须是数组")
    if not isinstance(output["review_focus"], list):
        raise ValueError(f"{stage_type}.review_focus 必须是数组")
    if not isinstance(output["evidence_safe"], bool):
        raise ValueError(f"{stage_type}.evidence_safe 必须是布尔值")

    return output


def validate_evidence_risk_output(output: dict[str, Any]) -> dict[str, Any]:
    if output["evidence_risk_level"] not in ALLOWED_RISK_LEVELS:
        raise ValueError(f"evidence_risk.evidence_risk_level 非法：{output['evidence_risk_level']}")
    for field_name in [
        "risk_tags",
        "risk_locations",
        "uncertain_symbols",
        "consistency_risks",
        "final_answer_risks",
        "grading_constraints",
    ]:
        if not isinstance(output[field_name], list):
            raise ValueError(f"evidence_risk.{field_name} 必须是数组")
    if "review_advice" in output or "evidence_safe" in output:
        raise ValueError("evidence_risk 不能输出 review_advice 或 evidence_safe")
    return output


def validate_method_analysis_output(output: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output["method_summary"], str):
        raise ValueError("method_analysis.method_summary 必须是字符串")
    if output["method_confidence"] not in ALLOWED_METHOD_CONFIDENCE:
        raise ValueError(f"method_analysis.method_confidence 非法：{output['method_confidence']}")
    if not isinstance(output["method_evidence_blocks"], list):
        raise ValueError("method_analysis.method_evidence_blocks 必须是数组")
    if "method_id" in output:
        raise ValueError("method_analysis 不能输出 method_id")
    return output


def validate_homework_review_output(output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验作业评价结果，确保评价字段稳定且不混入分数或评分点。

    参数：
        output (dict[str, Any]): 作业评价模型返回的结构化结果。

    返回：
        dict[str, Any]: 通过结构和枚举校验的作业评价结果。
    """
    if output["answer_status"] not in ALLOWED_HOMEWORK_ANSWER_STATUSES:
        raise ValueError(f"homework_review.answer_status 非法：{output['answer_status']}")
    if not isinstance(output["summary"], str):
        raise ValueError("homework_review.summary 必须是字符串")

    for field_name in ["strengths", "improvement_suggestions", "risk_tags", "review_focus"]:
        if not isinstance(output[field_name], list) or any(not isinstance(item, str) for item in output[field_name]):
            raise ValueError(f"homework_review.{field_name} 必须是字符串数组")

    if not isinstance(output["issues"], list):
        raise ValueError("homework_review.issues 必须是数组")
    required_issue_fields = {"type", "location", "evidence", "explanation", "correction_guidance"}
    for issue_index, issue in enumerate(output["issues"]):
        if not isinstance(issue, dict):
            raise ValueError(f"homework_review.issues[{issue_index}] 必须是 JSON 对象")
        missing_fields = sorted(required_issue_fields - set(issue.keys()))
        if missing_fields:
            raise ValueError(
                f"homework_review.issues[{issue_index}] 缺少必要字段：{', '.join(missing_fields)}"
            )
        if issue["type"] not in ALLOWED_HOMEWORK_ISSUE_TYPES:
            raise ValueError(f"homework_review.issues[{issue_index}].type 非法：{issue['type']}")
        for field_name in required_issue_fields - {"type"}:
            if not isinstance(issue[field_name], str):
                raise ValueError(f"homework_review.issues[{issue_index}].{field_name} 必须是字符串")

    # 初步可信度字段与独立复核阶段共用枚举，关闭复核模型时也能维持稳定顶层出参。
    validate_review_decision_output("homework_review", output)

    if "suggested_score" in output or "score_points" in output or "deductions" in output:
        raise ValueError("homework_review 不得输出分数、评分点或扣分项")
    return output
