from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .grading_api import build_provider

from app.pipelines.question_type import normalize_question_type
from app.providers.base import unwrap_provider_response
from app.providers.latex_quality import find_text_latex_issues


ALLOWED_GRADING_MODES = {"exam", "homework"}
ALLOWED_CONTENT_MODES = {"stem_only", "solution_unannotated", "solution_annotated"}
ALLOWED_CONFIDENCE_LEVELS = {"high", "medium", "low"}
DEFAULT_EXAM_SCORE = 10.0
SCORE_TOLERANCE = 1e-9

# 题目正文比普通自然语言更容易出现短公式，因此补充共享检查器未覆盖的高置信度表达式模式。
BARE_ARITHMETIC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-zΑ-Ωα-ω]|\d+(?:\.\d+)?)\s*[+\-−*/×÷]\s*"
    r"(?:[A-Za-zΑ-Ωα-ω]|\d+(?:\.\d+)?)(?![A-Za-z0-9])"
)
BARE_FUNCTION_PATTERN = re.compile(r"(?<![A-Za-z])[A-Za-zΑ-Ωα-ω]+\s*\(\s*[A-Za-zΑ-Ωα-ω0-9]")
BARE_SINGLE_VARIABLE_PATTERN = re.compile(r"(?<![A-Za-z])[A-Za-zΑ-Ωα-ω](?![A-Za-z])")
UNICODE_SUPERSCRIPT_PATTERN = re.compile(r"[A-Za-zΑ-Ωα-ω0-9)\]}][⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+")
PLAIN_MATH_ANSWER_PATTERN = re.compile(
    r"^[\sA-Za-zΑ-Ωα-ω0-9_^{},.()+\-−*/=<>≤≥×÷\\]+$"
)
FORMULA_FREE_LABEL_MARKER_PATTERN = re.compile(r"[$\\^_=<>≤≥∑∏∫√∞±×÷∂∇→⇒⇔]|[A-Za-zΑ-Ωα-ω]+\s*\(")

QUESTION_TITLE_FALLBACKS = {
    "calculation": "数学计算题",
    "proof": "数学证明题",
    "discussion": "数学讨论题",
    "mixed": "综合数学题",
    "fill_blank": "数学填空题",
    "unknown": "数学题目",
}


def generate_question_from_image(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：从题目图片识别题干和已有配置，并仅对图片中缺失的答案或评分路径调用 AI 补齐。

    参数：
        payload (dict[str, Any]): 图片生成入参，包含 grading_mode，以及 image_path 或 image_url。

    返回：
        dict[str, Any]: 可供老师确认的题目配置；失败时返回稳定的 failed 结构。
    """
    try:
        context = build_image_generation_context(payload)
        provider = build_provider()
        model_stages: list[dict[str, Any]] = []

        parser_response = provider.run_question_image_parser(context)
        parser_output, raw_output_path, model_name = unwrap_provider_response(provider, parser_response)
        parser_output = normalize_image_parser_output(parser_output)
        model_stages.append(
            build_model_stage(
                stage_type="question_image_parser",
                model_name=model_name,
                raw_output_path=raw_output_path,
            )
        )

        # 图片接口不接收分数。考题优先采用截图分数，识别不到时按已确认规则回退为 10 分。
        score, score_source, score_warnings = resolve_image_score(
            grading_mode=context["grading_mode"],
            detected_score=parser_output.get("detected_score"),
        )
        warnings = merge_string_lists(parser_output.get("warnings", []), score_warnings)

        extracted_answers = normalize_standard_answers(parser_output.get("standard_answers"))
        extracted_paths = normalize_scoring_paths(parser_output.get("scoring_paths"))
        extracted_solution = parser_output["solution_text"]
        allowed_generation_fields: list[str] = []
        if not extracted_answers:
            allowed_generation_fields.append("standard_answers")
        if context["grading_mode"] == "exam" and not extracted_solution:
            allowed_generation_fields.append("solution_text")
        if context["grading_mode"] == "exam" and not extracted_paths:
            allowed_generation_fields.append("scoring_paths")

        generated_output: dict[str, Any] = {}
        if allowed_generation_fields:
            generator_context = build_generator_context(
                base_context=context,
                question_title=parser_output["question_title"],
                question_content=parser_output["question_content"],
                question_type=parser_output["question_type"],
                question_type_confidence=parser_output["question_type_confidence"],
                score=score,
                standard_answers=extracted_answers,
                solution_text=extracted_solution,
                scoring_paths=extracted_paths,
                allowed_generation_fields=allowed_generation_fields,
            )
            generator_response = provider.run_question_config_generator(generator_context)
            generated_output, raw_output_path, model_name = unwrap_provider_response(provider, generator_response)
            generated_output = normalize_generator_output(generated_output)
            model_stages.append(
                build_model_stage(
                    stage_type="question_config_generator",
                    model_name=model_name,
                    raw_output_path=raw_output_path,
                )
            )

        answer_is_generated = "standard_answers" in allowed_generation_fields
        scoring_is_generated = "scoring_paths" in allowed_generation_fields
        standard_answers = (
            normalize_standard_answers(generated_output.get("standard_answers"))
            if answer_is_generated
            else extracted_answers
        )
        if not standard_answers:
            raise ValueError("未能识别或生成标准答案")

        solution_text = (
            str(generated_output.get("solution_text") or "").strip()
            if "solution_text" in allowed_generation_fields
            else extracted_solution
        )
        if context["grading_mode"] == "homework":
            solution_text = ""

        if context["grading_mode"] == "exam":
            scoring_paths = (
                normalize_scoring_paths(generated_output.get("scoring_paths"))
                if scoring_is_generated
                else extracted_paths
            )
            if not scoring_paths:
                raise ValueError("考题未能识别或生成评分路径")

            # AI 生成路径必须严格等于总分；老师原图中的标注只提示差异，不能由程序擅自改写。
            if scoring_is_generated:
                validate_scoring_path_totals(scoring_paths, score)
            else:
                warnings = merge_string_lists(warnings, collect_scoring_total_warnings(scoring_paths, score))
        else:
            scoring_paths = []

        warnings = merge_string_lists(warnings, normalize_string_list(generated_output.get("warnings")))
        if score_source == "default":
            warnings = merge_string_lists(warnings, ["截图中未识别到有效分值，已使用默认值 10 分。"])
        if answer_is_generated:
            warnings = merge_string_lists(warnings, ["截图中未发现可靠标准答案，已由 AI 自动生成。"])

        success_response = build_success_response(
            grading_mode=context["grading_mode"],
            question_title=parser_output["question_title"],
            question_content=parser_output["question_content"],
            question_type=parser_output["question_type"],
            question_type_confidence=parser_output["question_type_confidence"],
            score=score,
            score_source=score_source,
            content_mode=parser_output["content_mode"],
            standard_answers=standard_answers,
            solution_text=solution_text,
            answer_is_generated=answer_is_generated,
            scoring_paths=scoring_paths,
            scoring_is_generated=scoring_is_generated if context["grading_mode"] == "exam" else None,
            warnings=warnings,
            model_stages=model_stages,
        )
        return ensure_question_generation_output_quality(
            provider=provider,
            context=context,
            response=success_response,
            model_stages=model_stages,
        )
    except Exception as exc:  # noqa: BLE001
        # 和现有批改入口保持一致：业务调用失败时返回结构化结果，不把异常直接抛给任务进程。
        return build_failed_response(payload, exc)


def generate_question_from_text(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：根据文本题干生成标题、建议题型、标准答案，以及考题所需的评分路径。

    参数：
        payload (dict[str, Any]): 文本生成入参；考题必须包含 score，作业不得依赖 score。

    返回：
        dict[str, Any]: 可供老师确认的完整题目配置；失败时返回稳定的 failed 结构。
    """
    try:
        context = build_text_generation_context(payload)
        provider = build_provider()
        allowed_generation_fields = ["question_title", "question_type", "standard_answers"]
        if context["grading_mode"] == "exam":
            allowed_generation_fields.extend(["solution_text", "scoring_paths"])

        generator_context = build_generator_context(
            base_context=context,
            question_title="",
            question_content=context["question_content"],
            question_type="unknown",
            question_type_confidence="low",
            score=context["score"],
            standard_answers=[],
            solution_text="",
            scoring_paths=[],
            allowed_generation_fields=allowed_generation_fields,
        )
        generator_response = provider.run_question_config_generator(generator_context)
        generated_output, raw_output_path, model_name = unwrap_provider_response(provider, generator_response)
        generated_output = normalize_generator_output(generated_output)

        standard_answers = normalize_standard_answers(generated_output.get("standard_answers"))
        if not standard_answers:
            raise ValueError("未能生成标准答案")

        if context["grading_mode"] == "exam":
            scoring_paths = normalize_scoring_paths(generated_output.get("scoring_paths"))
            if not scoring_paths:
                raise ValueError("考题未能生成评分路径")
            validate_scoring_path_totals(scoring_paths, context["score"])
            scoring_is_generated: bool | None = True
        else:
            scoring_paths = []
            scoring_is_generated = None

        question_type = normalize_question_type(generated_output.get("question_type"))
        question_title = normalize_question_title(generated_output.get("question_title"), question_type)
        question_type_confidence = normalize_confidence(generated_output.get("question_type_confidence"))
        model_stages = [
            build_model_stage(
                stage_type="question_config_generator",
                model_name=model_name,
                raw_output_path=raw_output_path,
            )
        ]
        success_response = build_success_response(
            grading_mode=context["grading_mode"],
            question_title=question_title,
            question_content=context["question_content"],
            question_type=question_type,
            question_type_confidence=question_type_confidence,
            score=context["score"],
            score_source="input" if context["grading_mode"] == "exam" else "not_applicable",
            content_mode="stem_only",
            standard_answers=standard_answers,
            solution_text=(
                str(generated_output.get("solution_text") or "").strip()
                if context["grading_mode"] == "exam"
                else ""
            ),
            answer_is_generated=True,
            scoring_paths=scoring_paths,
            scoring_is_generated=scoring_is_generated,
            warnings=normalize_string_list(generated_output.get("warnings")),
            model_stages=model_stages,
        )
        return ensure_question_generation_output_quality(
            provider=provider,
            context=context,
            response=success_response,
            model_stages=model_stages,
        )
    except Exception as exc:  # noqa: BLE001
        return build_failed_response(payload, exc)


def build_image_generation_context(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验图片生成入参，并转换为视觉 provider 可直接使用的上下文。

    参数：
        payload (dict[str, Any]): 外部图片生成请求。

    返回：
        dict[str, Any]: 包含图片来源、业务类型和审计标识的内部上下文。
    """
    require_payload_object(payload)
    grading_mode = require_grading_mode(payload)
    if "score" in payload and payload.get("score") is not None:
        raise ValueError("图片生成方法不接收 score；考题分数从截图识别，识别失败时默认 10 分")

    image_path = str(payload.get("image_path") or "").strip()
    image_url = str(payload.get("image_url") or "").strip()
    if bool(image_path) == bool(image_url):
        raise ValueError("image_path 和 image_url 必须且只能提供一个")
    if image_url and not image_url.startswith(("http://", "https://")):
        raise ValueError("image_url 必须是 http:// 或 https:// 链接")

    request_id = str(payload.get("request_id") or f"question_generation_{uuid4().hex}")
    return {
        "project_id": str(payload.get("project_id") or "question_generation"),
        "id": request_id,
        "grading_mode": grading_mode,
        "question": {"id": request_id},
        "answer_image": {"file_path": image_path, "image_url": image_url},
        # 该字段只为 mock 自动化测试提供可控图片识别结果，真实 provider 不会读取它。
        "mock_image_parse_output": deepcopy(payload.get("mock_image_parse_output")),
    }


def build_text_generation_context(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：校验题干生成入参，并规范化考题分数与作业无分数语义。

    参数：
        payload (dict[str, Any]): 外部文本生成请求。

    返回：
        dict[str, Any]: 文本题目生成内部上下文。
    """
    require_payload_object(payload)
    grading_mode = require_grading_mode(payload)
    question_content = str(payload.get("question_content") or "").strip()
    if not question_content:
        raise ValueError("question_content 不能为空")

    if grading_mode == "exam":
        score = parse_positive_score(payload.get("score"), "score")
    else:
        if "score" in payload and payload.get("score") is not None:
            raise ValueError("作业题干生成方法不接收 score")
        score = None

    request_id = str(payload.get("request_id") or f"question_generation_{uuid4().hex}")
    return {
        "project_id": str(payload.get("project_id") or "question_generation"),
        "id": request_id,
        "grading_mode": grading_mode,
        "question_content": question_content,
        "score": score,
        "question": {"id": request_id},
    }


def build_generator_context(
    base_context: dict[str, Any],
    question_title: str,
    question_content: str,
    question_type: str,
    question_type_confidence: str,
    score: float | None,
    standard_answers: list[str],
    solution_text: str,
    scoring_paths: list[dict[str, Any]],
    allowed_generation_fields: list[str],
) -> dict[str, Any]:
    """
    功能：构造文本生成模型上下文，并明确声明模型允许补齐的字段。

    参数：
        base_context (dict[str, Any]): 图片或文本入口建立的基础上下文。
        question_title (str): 已识别的题目标题，文本入口可为空。
        question_content (str): 规范化题干。
        question_type (str): 已识别题型，未知时为 unknown。
        question_type_confidence (str): 题型识别置信度。
        score (float | None): 考题总分；作业为 None。
        standard_answers (list[str]): 已存在的标准答案。
        solution_text (str): 已存在的参考解题过程。
        scoring_paths (list[dict[str, Any]]): 已存在的评分路径。
        allowed_generation_fields (list[str]): 本次允许模型生成的字段白名单。

    返回：
        dict[str, Any]: provider 可消费的题目配置生成上下文。
    """
    return {
        "project_id": base_context["project_id"],
        "id": base_context["id"],
        "grading_mode": base_context["grading_mode"],
        "question": {
            "id": base_context["id"],
            "question_title": question_title,
            "question_content": question_content,
            "question_type": question_type,
            "question_type_confidence": question_type_confidence,
            "score": score,
        },
        "existing_content": {
            "standard_answers": deepcopy(standard_answers),
            "solution_text": solution_text,
            "scoring_paths": deepcopy(scoring_paths),
        },
        "allowed_generation_fields": list(allowed_generation_fields),
    }


def normalize_image_parser_output(output: Any) -> dict[str, Any]:
    """
    功能：校验并规范化图片识别模型输出，防止缺失题干或返回非法场景。

    参数：
        output (Any): provider 返回的图片识别结果。

    返回：
        dict[str, Any]: 字段稳定的图片识别结果。
    """
    if not isinstance(output, dict):
        raise ValueError("question_image_parser 输出必须是 JSON 对象")

    question_content = str(output.get("question_content") or "").strip()
    if not question_content:
        raise ValueError("图片中未识别到有效题干")

    content_mode = str(output.get("content_mode") or "").strip().lower()
    if content_mode not in ALLOWED_CONTENT_MODES:
        raise ValueError("content_mode 必须是 stem_only、solution_unannotated 或 solution_annotated")

    standard_answers = normalize_standard_answers(output.get("standard_answers"))
    scoring_paths = normalize_scoring_paths(output.get("scoring_paths"))
    if content_mode == "stem_only" and (standard_answers or scoring_paths):
        raise ValueError("stem_only 场景不得包含已识别答案或评分路径")
    if content_mode == "solution_unannotated" and not standard_answers:
        raise ValueError("solution_unannotated 场景必须包含从作答中提取的最终答案")
    if content_mode == "solution_unannotated" and scoring_paths:
        raise ValueError("solution_unannotated 场景不得包含评分路径")
    if content_mode == "solution_annotated" and (not standard_answers or not scoring_paths):
        raise ValueError("solution_annotated 场景必须同时包含标准答案和评分路径")

    question_type = normalize_question_type(output.get("question_type"))
    return {
        "question_title": normalize_question_title(output.get("question_title"), question_type),
        "question_content": question_content,
        "question_type": question_type,
        "question_type_confidence": normalize_confidence(output.get("question_type_confidence")),
        "detected_score": output.get("detected_score"),
        "content_mode": content_mode,
        "standard_answers": standard_answers,
        "solution_text": str(output.get("solution_text") or "").strip(),
        "scoring_paths": scoring_paths,
        "warnings": normalize_string_list(output.get("warnings")),
    }


def normalize_generator_output(output: Any) -> dict[str, Any]:
    """
    功能：把配置生成模型输出收敛为稳定字段，具体必填性由入口按场景判断。

    参数：
        output (Any): provider 返回的题目配置生成结果。

    返回：
        dict[str, Any]: 规范化后的生成结果。
    """
    if not isinstance(output, dict):
        raise ValueError("question_config_generator 输出必须是 JSON 对象")
    return {
        "question_title": str(output.get("question_title") or "").strip(),
        "question_type": normalize_question_type(output.get("question_type")),
        "question_type_confidence": normalize_confidence(output.get("question_type_confidence")),
        "standard_answers": normalize_standard_answers(output.get("standard_answers")),
        "solution_text": str(output.get("solution_text") or "").strip(),
        "scoring_paths": normalize_scoring_paths(output.get("scoring_paths")),
        "warnings": normalize_string_list(output.get("warnings")),
    }


def normalize_scoring_paths(raw_paths: Any) -> list[dict[str, Any]]:
    """
    功能：校验评分路径结构并补齐稳定 ID，不改写评分语义和分值。

    参数：
        raw_paths (Any): 模型识别或生成的原始评分路径。

    返回：
        list[dict[str, Any]]: 结构稳定、路径 ID 和评分点 ID 唯一的评分路径。
    """
    if raw_paths is None:
        return []
    if not isinstance(raw_paths, list):
        raise ValueError("scoring_paths 必须是数组")

    normalized_paths: list[dict[str, Any]] = []
    seen_path_ids: set[str] = set()
    for path_index, raw_path in enumerate(raw_paths, start=1):
        if not isinstance(raw_path, dict):
            raise ValueError(f"scoring_paths[{path_index - 1}] 必须是 JSON 对象")

        path_id = str(raw_path.get("id") or f"path_{path_index}").strip()
        if path_id in seen_path_ids:
            raise ValueError(f"scoring_paths 存在重复路径 ID：{path_id}")
        seen_path_ids.add(path_id)

        raw_points = raw_path.get("points")
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError(f"评分路径 {path_id} 的 points 必须是非空数组")

        normalized_points: list[dict[str, Any]] = []
        seen_point_ids: set[str] = set()
        for point_index, raw_point in enumerate(raw_points, start=1):
            if not isinstance(raw_point, dict):
                raise ValueError(f"评分路径 {path_id} 的第 {point_index} 个评分点必须是 JSON 对象")
            point_id = str(raw_point.get("id") or f"{path_id}_point_{point_index}").strip()
            if point_id in seen_point_ids:
                raise ValueError(f"评分路径 {path_id} 存在重复评分点 ID：{point_id}")
            seen_point_ids.add(point_id)

            description = str(raw_point.get("description") or "").strip()
            if not description:
                raise ValueError(f"评分点 {point_id} 的 description 不能为空")
            point_score = parse_positive_score(raw_point.get("score"), f"评分点 {point_id}.score")
            normalized_points.append(
                {
                    "id": point_id,
                    "score": normalize_number(point_score),
                    "description": description,
                }
            )

        normalized_paths.append(
            {
                "id": path_id,
                "name": str(raw_path.get("name") or f"解法 {path_index}").strip(),
                "reference_solution": str(raw_path.get("reference_solution") or "").strip(),
                "applicability": str(raw_path.get("applicability") or "").strip(),
                "points": normalized_points,
            }
        )
    return normalized_paths


def validate_scoring_path_totals(scoring_paths: list[dict[str, Any]], score: float | None) -> None:
    """
    功能：严格校验每条 AI 生成评分路径的分值合计等于考题总分。

    参数：
        scoring_paths (list[dict[str, Any]]): 待校验的评分路径。
        score (float | None): 当前考题总分。

    返回：
        None: 校验通过时无返回值，失败时抛出 ValueError。
    """
    if score is None:
        raise ValueError("校验评分路径时缺少考题总分")
    for path in scoring_paths:
        path_total = sum(float(point["score"]) for point in path["points"])
        if abs(path_total - score) >= SCORE_TOLERANCE:
            raise ValueError(
                f"评分路径 {path['id']} 的评分点合计必须等于总分：合计={path_total:g}，总分={score:g}"
            )


def collect_scoring_total_warnings(
    scoring_paths: list[dict[str, Any]],
    score: float | None,
) -> list[str]:
    """
    功能：检查图片中老师原始评分标注的分值合计，只告警而不自动修改。

    参数：
        scoring_paths (list[dict[str, Any]]): 从图片原样识别的评分路径。
        score (float | None): 截图识别或默认得到的考题总分。

    返回：
        list[str]: 需要老师人工确认的分值差异说明。
    """
    if score is None:
        return []
    warnings: list[str] = []
    for path in scoring_paths:
        path_total = sum(float(point["score"]) for point in path["points"])
        if abs(path_total - score) >= SCORE_TOLERANCE:
            warnings.append(
                f"图片原始评分路径 {path['id']} 的评分点合计为 {path_total:g} 分，与题目总分 {score:g} 分不一致，请人工确认。"
            )
    return warnings


def resolve_image_score(grading_mode: str, detected_score: Any) -> tuple[float | None, str, list[str]]:
    """
    功能：按照“图片优先、识别失败默认 10 分”的规则确定考题分数。

    参数：
        grading_mode (str): exam 或 homework。
        detected_score (Any): 视觉模型从图片中识别出的原始分数。

    返回：
        tuple[float | None, str, list[str]]: 分数、分数来源和解析警告。
    """
    if grading_mode == "homework":
        return None, "not_applicable", []
    try:
        score = parse_positive_score(detected_score, "detected_score")
        return score, "image_extracted", []
    except ValueError:
        # 截图没有分数或识别成非法值时统一使用缺省值，避免生成链路因非核心字段中断。
        return DEFAULT_EXAM_SCORE, "default", []


def build_success_response(
    grading_mode: str,
    question_title: str,
    question_content: str,
    question_type: str,
    question_type_confidence: str,
    score: float | None,
    score_source: str,
    content_mode: str,
    standard_answers: list[str],
    solution_text: str,
    answer_is_generated: bool,
    scoring_paths: list[dict[str, Any]],
    scoring_is_generated: bool | None,
    warnings: list[str],
    model_stages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：构造图片和文本方法共用的稳定成功响应。

    参数：
        grading_mode (str): exam 或 homework。
        question_title (str): 简短题目标题。
        question_content (str): 完整题干。
        question_type (str): 规范化建议题型。
        question_type_confidence (str): 题型识别置信度。
        score (float | None): 考题总分或作业空值。
        score_source (str): 分数来源。
        content_mode (str): 图片内容场景；文本方法固定为 stem_only。
        standard_answers (list[str]): 最终标准答案数组。
        solution_text (str): 考题标准解题过程；作业固定为空字符串。
        answer_is_generated (bool): 标准答案是否由 AI 补齐。
        scoring_paths (list[dict[str, Any]]): 考题评分路径。
        scoring_is_generated (bool | None): 评分路径是否由 AI 补齐；作业为 None。
        warnings (list[str]): 需要老师关注的提示。
        model_stages (list[dict[str, Any]]): 实际发生的模型调用审计信息。

    返回：
        dict[str, Any]: 对接方可直接展示并等待人工确认的结果。
    """
    return {
        "status": "success",
        "grading_mode": grading_mode,
        "question_title": question_title,
        "question_content": question_content,
        "question_type": question_type,
        "question_type_source": "ai_inferred",
        "question_type_confidence": question_type_confidence,
        "score": normalize_number(score) if score is not None else None,
        "score_source": score_source,
        "content_mode": content_mode,
        "standard_answers": standard_answers,
        "solution_text": solution_text,
        "is_answer_ai_generated": answer_is_generated,
        "answer_source": "ai_generated" if answer_is_generated else "image_extracted",
        "scoring_paths": scoring_paths,
        "is_scoring_paths_ai_generated": scoring_is_generated,
        "scoring_paths_source": (
            "not_applicable"
            if scoring_is_generated is None
            else "ai_generated"
            if scoring_is_generated
            else "image_extracted"
        ),
        "warnings": warnings,
        "model_call_count": len(model_stages),
        "model_stages": model_stages,
    }


def ensure_question_generation_output_quality(
    provider: Any,
    context: dict[str, Any],
    response: dict[str, Any],
    model_stages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：统一检查两个题目生成入口的最终响应，并在必要时最多调用模型修复一次。

    参数：
        provider (Any): 当前模型 provider，必须实现题目输出格式修复方法。
        context (dict[str, Any]): 当前请求上下文，用于模型调用和审计落盘。
        response (dict[str, Any]): 尚未通过质量门的完整成功响应。
        model_stages (list[dict[str, Any]]): 已发生的模型调用审计列表。

    返回：
        dict[str, Any]: 已通过 LaTeX 与名称类字段检查的完整成功响应。
    """
    quality_issues = find_question_generation_quality_issues(response)
    if not quality_issues:
        return response

    repair_context = {
        "project_id": context["project_id"],
        "id": context["id"],
        "question": {"id": context["id"]},
        "original_output": deepcopy(response),
        "validation_issues": quality_issues,
    }
    repair_response = provider.run_question_output_formatter(repair_context)
    repaired_output, raw_output_path, model_name = unwrap_provider_response(provider, repair_response)
    if not isinstance(repaired_output, dict):
        raise ValueError("question_output_formatter 输出必须是完整 JSON 对象")

    # 先验证业务结构完全不变，再只接收允许修改的展示文本，避免修复模型改动分值、ID 或来源状态。
    if build_question_generation_structure_signature(response) != build_question_generation_structure_signature(
        repaired_output
    ):
        raise ValueError("题目配置格式修复改变了答案数量、评分结构或业务状态")
    merged_response = merge_repaired_display_texts(response, repaired_output)
    merged_response = apply_formula_free_label_fallbacks(merged_response)

    remaining_issues = find_question_generation_quality_issues(merged_response)
    if remaining_issues:
        issue_summary = "；".join(remaining_issues[:10])
        raise ValueError(f"题目配置中的数学表达式仍不满足 LaTeX 要求：{issue_summary}")

    model_stages.append(
        build_model_stage(
            stage_type="question_output_formatter",
            model_name=model_name,
            raw_output_path=raw_output_path,
        )
    )
    # 调用次数只由程序依据真实调用记录重建，不接受修复模型回写的审计值。
    merged_response["model_stages"] = model_stages
    merged_response["model_call_count"] = len(model_stages)
    return merged_response


def find_question_generation_quality_issues(output: dict[str, Any]) -> list[str]:
    """
    功能：检查题目配置中的裸公式、定界符错误，以及名称类字段中的公式。

    参数：
        output (dict[str, Any]): 两个公开方法组装出的完整成功响应。

    返回：
        list[str]: 带字段路径的质量问题；空列表表示检查通过。
    """
    issues: list[str] = []
    for field_path, text_value, strict_answer in iter_question_generation_math_texts(output):
        text_issues = find_question_text_latex_issues(text_value, strict_answer=strict_answer)
        if text_issues:
            issues.append(f"{field_path}: {', '.join(text_issues)}")
        if len(issues) >= 50:
            return issues

    question_title = str(output.get("question_title") or "")
    if not is_formula_free_label(question_title, max_length=30):
        issues.append("question_title: 必须是简短且不含公式的自然语言标题")

    scoring_paths = output.get("scoring_paths")
    if isinstance(scoring_paths, list):
        for path_index, scoring_path in enumerate(scoring_paths):
            if not isinstance(scoring_path, dict):
                continue
            path_name = str(scoring_path.get("name") or "")
            if not is_formula_free_label(path_name, max_length=30):
                issues.append(f"scoring_paths[{path_index}].name: 必须是不含公式的自然语言名称")
    return issues[:50]


def iter_question_generation_math_texts(output: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """
    功能：按白名单提取题目配置中允许出现数学表达式的展示字段。

    参数：
        output (dict[str, Any]): 完整题目配置响应。

    返回：
        list[tuple[str, str, bool]]: 字段路径、文本及是否按标准答案严格检查组成的列表。
    """
    display_texts: list[tuple[str, str, bool]] = []
    for field_name in ("question_content", "solution_text"):
        field_value = output.get(field_name)
        if isinstance(field_value, str) and field_value:
            display_texts.append((field_name, field_value, False))

    for answer_index, answer_value in enumerate(output.get("standard_answers") or []):
        if isinstance(answer_value, str) and answer_value:
            display_texts.append((f"standard_answers[{answer_index}]", answer_value, True))
    for warning_index, warning_value in enumerate(output.get("warnings") or []):
        if isinstance(warning_value, str) and warning_value:
            display_texts.append((f"warnings[{warning_index}]", warning_value, False))

    for path_index, scoring_path in enumerate(output.get("scoring_paths") or []):
        if not isinstance(scoring_path, dict):
            continue
        for field_name in ("reference_solution", "applicability"):
            field_value = scoring_path.get(field_name)
            if isinstance(field_value, str) and field_value:
                display_texts.append((f"scoring_paths[{path_index}].{field_name}", field_value, False))
        for point_index, point in enumerate(scoring_path.get("points") or []):
            if isinstance(point, dict) and isinstance(point.get("description"), str):
                display_texts.append(
                    (
                        f"scoring_paths[{path_index}].points[{point_index}].description",
                        point["description"],
                        False,
                    )
                )
    return display_texts


def find_question_text_latex_issues(text: str, strict_answer: bool = False) -> list[str]:
    """
    功能：在共享 LaTeX 检查基础上补充题目领域中的算式、函数和短答案检查。

    参数：
        text (str): 待检查文本。
        strict_answer (bool): 是否把纯数字或纯数学字符答案也视为必须包裹的公式。

    返回：
        list[str]: 当前文本命中的去重问题类型。
    """
    issues = list(find_text_latex_issues(text))
    outside_text = remove_latex_segments_for_question_check(text)
    if BARE_ARITHMETIC_PATTERN.search(outside_text):
        issues.append("算式未放入公式定界符")
    if BARE_FUNCTION_PATTERN.search(outside_text):
        issues.append("函数表达式未放入公式定界符")
    if UNICODE_SUPERSCRIPT_PATTERN.search(outside_text):
        issues.append("Unicode 上标表达式未放入公式定界符")
    if BARE_SINGLE_VARIABLE_PATTERN.search(outside_text):
        issues.append("单独变量未放入公式定界符")
    if strict_answer and "$" not in text and PLAIN_MATH_ANSWER_PATTERN.fullmatch(text.strip()):
        issues.append("数学答案未放入公式定界符")
    return list(dict.fromkeys(issues))


def remove_latex_segments_for_question_check(text: str) -> str:
    """
    功能：用空格替换已正确包裹的公式，供题目领域的补充正则只检查公式外文本。

    参数：
        text (str): 可能同时包含自然语言和公式的展示文本。

    返回：
        str: 已移除 `$...$` 与 `$$...$$` 内容的普通文本。
    """
    characters: list[str] = []
    delimiter = ""
    index = 0
    while index < len(text):
        if not delimiter and text.startswith("$$", index):
            delimiter = "$$"
            index += 2
            continue
        if not delimiter and text[index] == "$":
            delimiter = "$"
            index += 1
            continue
        if delimiter and text.startswith(delimiter, index):
            delimiter = ""
            characters.append(" ")
            index += 2 if text.startswith("$$", index) else 1
            continue
        if not delimiter:
            characters.append(text[index])
        index += 1
    return "".join(characters)


def is_formula_free_label(label: str, max_length: int) -> bool:
    """
    功能：判断标题或解法名称是否为简短、单行且不含明显公式的自然语言标签。

    参数：
        label (str): 待检查的名称类文本。
        max_length (int): 允许的最大字符数。

    返回：
        bool: 满足名称字段要求时返回 True。
    """
    compact_label = label.strip()
    if not compact_label or len(compact_label) > max_length or "\n" in compact_label or "\r" in compact_label:
        return False
    if FORMULA_FREE_LABEL_MARKER_PATTERN.search(compact_label):
        return False
    if BARE_SINGLE_VARIABLE_PATTERN.search(compact_label):
        return False
    if BARE_ARITHMETIC_PATTERN.search(compact_label) or UNICODE_SUPERSCRIPT_PATTERN.search(compact_label):
        return False
    return not find_text_latex_issues(compact_label)


def build_formula_free_title_fallback(question_type: str) -> str:
    """
    功能：根据题型生成绝不包含公式的标题兜底，避免再次截取含公式题干。

    参数：
        question_type (str): 已规范化题型。

    返回：
        str: 可安全用于列表展示的自然语言标题。
    """
    return QUESTION_TITLE_FALLBACKS.get(question_type, QUESTION_TITLE_FALLBACKS["unknown"])


def apply_formula_free_label_fallbacks(output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：修复模型仍未改好的名称字段，并保证兜底过程不触碰正文数学内容。

    参数：
        output (dict[str, Any]): 已合并模型格式修复文本的响应。

    返回：
        dict[str, Any]: 标题和路径名称均不含公式的响应副本。
    """
    normalized_output = deepcopy(output)
    if not is_formula_free_label(str(normalized_output.get("question_title") or ""), max_length=30):
        normalized_output["question_title"] = build_formula_free_title_fallback(
            str(normalized_output.get("question_type") or "unknown")
        )
    for path_index, scoring_path in enumerate(normalized_output.get("scoring_paths") or [], start=1):
        if isinstance(scoring_path, dict) and not is_formula_free_label(
            str(scoring_path.get("name") or ""), max_length=30
        ):
            scoring_path["name"] = f"解法 {path_index}"
    return normalized_output


def build_question_generation_structure_signature(output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：提取格式修复前后必须保持不变的业务结构、分值、ID 和来源状态。

    参数：
        output (dict[str, Any]): 修复前或修复后的完整题目配置。

    返回：
        dict[str, Any]: 不包含允许修复的展示文本的结构签名。
    """
    path_signatures: list[dict[str, Any]] = []
    for scoring_path in output.get("scoring_paths") or []:
        if not isinstance(scoring_path, dict):
            path_signatures.append({"invalid_path": True})
            continue
        point_signatures = []
        for point in scoring_path.get("points") or []:
            if isinstance(point, dict):
                point_signatures.append({"id": point.get("id"), "score": point.get("score")})
            else:
                point_signatures.append({"invalid_point": True})
        path_signatures.append({"id": scoring_path.get("id"), "points": point_signatures})

    protected_fields = (
        "status",
        "grading_mode",
        "question_type",
        "question_type_source",
        "question_type_confidence",
        "score",
        "score_source",
        "content_mode",
        "is_answer_ai_generated",
        "answer_source",
        "is_scoring_paths_ai_generated",
        "scoring_paths_source",
        "model_call_count",
        "model_stages",
    )
    return {
        "top_level_keys": sorted(output.keys()),
        "protected_fields": {field_name: output.get(field_name) for field_name in protected_fields},
        "standard_answer_count": len(output.get("standard_answers") or []),
        "warning_count": len(output.get("warnings") or []),
        "scoring_paths": path_signatures,
    }


def merge_repaired_display_texts(
    original_output: dict[str, Any],
    repaired_output: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：仅从修复结果接收允许变化的展示文本，其余业务字段始终保留原值。

    参数：
        original_output (dict[str, Any]): 修复前的可信业务响应。
        repaired_output (dict[str, Any]): 已通过结构签名检查的模型修复结果。

    返回：
        dict[str, Any]: 业务结构来自原响应、展示文本来自修复响应的安全合并结果。
    """
    merged_output = deepcopy(original_output)
    for field_name in ("question_title", "question_content", "solution_text"):
        merged_output[field_name] = str(repaired_output.get(field_name) or "").strip()
    merged_output["standard_answers"] = [str(value).strip() for value in repaired_output["standard_answers"]]
    merged_output["warnings"] = [str(value).strip() for value in repaired_output["warnings"]]

    for path_index, repaired_path in enumerate(repaired_output["scoring_paths"]):
        merged_path = merged_output["scoring_paths"][path_index]
        for field_name in ("name", "reference_solution", "applicability"):
            merged_path[field_name] = str(repaired_path.get(field_name) or "").strip()
        for point_index, repaired_point in enumerate(repaired_path.get("points") or []):
            merged_path["points"][point_index]["description"] = str(
                repaired_point.get("description") or ""
            ).strip()
    return merged_output


def build_failed_response(payload: Any, exc: Exception) -> dict[str, Any]:
    """
    功能：构造题目生成失败时的稳定返回结构，避免调用方解析异常形态。

    参数：
        payload (Any): 原始调用入参。
        exc (Exception): 捕获到的校验或模型调用异常。

    返回：
        dict[str, Any]: status 为 failed 的结构化结果。
    """
    grading_mode = ""
    if isinstance(payload, dict):
        grading_mode = str(payload.get("grading_mode") or "").strip().lower()
    return {
        "status": "failed",
        "grading_mode": grading_mode,
        "question_title": "",
        "question_content": "",
        "question_type": "unknown",
        "question_type_source": "ai_inferred",
        "question_type_confidence": "low",
        "score": None,
        "score_source": "unavailable",
        "content_mode": "",
        "standard_answers": [],
        "solution_text": "",
        "is_answer_ai_generated": None,
        "answer_source": "unavailable",
        "scoring_paths": [],
        "is_scoring_paths_ai_generated": None,
        "scoring_paths_source": "unavailable",
        "warnings": [],
        "model_call_count": 0,
        "model_stages": [],
        "error": {
            "code": "QUESTION_GENERATION_FAILED",
            "message": str(exc),
        },
    }


def build_model_stage(stage_type: str, model_name: str, raw_output_path: str) -> dict[str, Any]:
    """
    功能：记录一次实际模型调用，便于调用方核对图片方法究竟执行了一次还是两次。

    参数：
        stage_type (str): 模型阶段名称。
        model_name (str): 实际模型名称。
        raw_output_path (str): 可选的原始输出审计文件路径。

    返回：
        dict[str, Any]: 精简模型调用审计对象。
    """
    return {
        "stage_type": stage_type,
        "model_name": model_name,
        "raw_output_path": raw_output_path,
    }


def require_payload_object(payload: Any) -> None:
    """
    功能：确保公开方法接收到 JSON 对象形态的字典。

    参数：
        payload (Any): 外部传入参数。

    返回：
        None: 类型正确时无返回值，错误时抛出 ValueError。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 JSON 对象")


def require_grading_mode(payload: dict[str, Any]) -> str:
    """
    功能：读取并校验作业/考题业务类型，禁止通过 score 是否存在进行隐式推断。

    参数：
        payload (dict[str, Any]): 外部题目生成入参。

    返回：
        str: exam 或 homework。
    """
    grading_mode = str(payload.get("grading_mode") or "").strip().lower()
    if grading_mode not in ALLOWED_GRADING_MODES:
        raise ValueError("grading_mode 必须是 exam 或 homework")
    return grading_mode


def normalize_standard_answers(raw_answers: Any) -> list[str]:
    """
    功能：清理标准答案数组中的空值并保持原有顺序。

    参数：
        raw_answers (Any): 模型返回的标准答案字段。

    返回：
        list[str]: 非空标准答案数组。
    """
    if raw_answers is None:
        return []
    if not isinstance(raw_answers, list):
        raise ValueError("standard_answers 必须是字符串数组")
    answers = [str(answer).strip() for answer in raw_answers if str(answer).strip()]
    return list(dict.fromkeys(answers))


def normalize_string_list(raw_values: Any) -> list[str]:
    """
    功能：把可选警告字段规范化为去重字符串数组。

    参数：
        raw_values (Any): 原始数组或空值。

    返回：
        list[str]: 去空、去重后的字符串数组。
    """
    if raw_values is None:
        return []
    if not isinstance(raw_values, list):
        raise ValueError("warnings 必须是字符串数组")
    values = [str(value).strip() for value in raw_values if str(value).strip()]
    return list(dict.fromkeys(values))


def merge_string_lists(*value_lists: list[str]) -> list[str]:
    """
    功能：按出现顺序合并多个字符串数组，并去除重复提示。

    参数：
        value_lists (list[str]): 一个或多个已规范化字符串数组。

    返回：
        list[str]: 合并去重后的数组。
    """
    merged: list[str] = []
    for values in value_lists:
        for value in values:
            if value not in merged:
                merged.append(value)
    return merged


def normalize_question_title(raw_title: Any, question_type: str) -> str:
    """
    功能：优先保留模型标题，缺失时根据题型生成不含公式的稳定兜底标题。

    参数：
        raw_title (Any): 模型生成或识别的标题。
        question_type (str): 已规范化的建议题型。

    返回：
        str: 非空题目标题。
    """
    title = str(raw_title or "").strip()
    if title:
        return title
    return build_formula_free_title_fallback(question_type)


def normalize_confidence(raw_confidence: Any) -> str:
    """
    功能：规范化题型置信度，非法或缺失值按低置信度处理。

    参数：
        raw_confidence (Any): 模型返回的置信度。

    返回：
        str: high、medium 或 low。
    """
    confidence = str(raw_confidence or "").strip().lower()
    return confidence if confidence in ALLOWED_CONFIDENCE_LEVELS else "low"


def parse_positive_score(raw_score: Any, field_name: str) -> float:
    """
    功能：把外部分值转换为正浮点数，并给出带字段名的明确错误。

    参数：
        raw_score (Any): 原始分值。
        field_name (str): 错误信息使用的字段名。

    返回：
        float: 大于 0 的规范化分值。
    """
    if raw_score is None or isinstance(raw_score, bool):
        raise ValueError(f"{field_name} 必须是大于 0 的数字")
    try:
        score = float(raw_score)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是大于 0 的数字") from exc
    if not math.isfinite(score) or score <= 0:
        raise ValueError(f"{field_name} 必须大于 0")
    return score


def normalize_number(value: float) -> int | float:
    """
    功能：把整数值浮点数转换为整数，避免 JSON 中出现无意义的 10.0。

    参数：
        value (float): 待规范化数字。

    返回：
        int | float: 数学整数返回 int，其他值保留 float。
    """
    return int(value) if float(value).is_integer() else float(value)
