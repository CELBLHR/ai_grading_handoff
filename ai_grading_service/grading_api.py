from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


PACKAGE_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PACKAGE_ROOT / "_runtime"
PROJECT_ROOT = next(
    candidate
    for candidate in [RUNTIME_ROOT, Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[2], Path.cwd()]
    if (candidate / "app").exists() or (candidate / "backend" / "app").exists()
)
IMPORT_ROOT = PROJECT_ROOT if (PROJECT_ROOT / "app").exists() else PROJECT_ROOT / "backend"
if str(IMPORT_ROOT) not in sys.path:
    # 第一版交付包优先使用包内 _runtime；开发目录下则回退复用仓库 backend/app。
    sys.path.insert(0, str(IMPORT_ROOT))

from app.pipelines.contracts import validate_stage_output  # noqa: E402
from app.pipelines.question_type import get_question_type  # noqa: E402
from app.pipelines.stages.evidence_risk import EvidenceRiskStage  # noqa: E402
from app.pipelines.stages.homework_review import HomeworkReviewStage  # noqa: E402
from app.pipelines.stages.homework_review_decision import HomeworkReviewDecisionStage  # noqa: E402
from app.pipelines.stages.method_analysis import MethodAnalysisStage  # noqa: E402
from app.pipelines.stages.review_decision import ReviewDecisionStage  # noqa: E402
from app.pipelines.stages.scoring import ScoringStage  # noqa: E402
from app.pipelines.stages.visual_ocr import VisualOcrStage  # noqa: E402
from app.providers.config import get_homework_review_mode, load_provider_config  # noqa: E402
from app.providers.mock import MockModelProvider  # noqa: E402
from app.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402


CancelChecker = Callable[[], bool]


def grade_answer(
    payload: dict[str, Any],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> dict[str, Any]:
    """
    功能：批改单个学生答题，并返回可由 Django 侧直接入库的结构化结果。

    参数：
        payload (dict[str, Any]): 单题批改入参，包含题目信息、学生信息和学生作答。
        cancel_event (Any | None): 可选取消事件，通常是 threading.Event。
        cancel_checker (CancelChecker | None): 可选取消检查回调，返回 True 表示已取消。

    返回：
        dict[str, Any]: 批改结果；成功、失败、取消都使用稳定结构返回。
    """
    try:
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_cancelled_response(payload)

        context = build_context(payload)
        previous_outputs: dict[str, dict[str, Any]] = {}
        stage_results: list[dict[str, Any]] = []
        provider = build_provider()

        if is_cancel_requested(cancel_event, cancel_checker):
            return build_cancelled_response(payload, context, previous_outputs, stage_results)

        visual_output, visual_stage_result = get_or_run_visual_output(payload, provider, context)
        previous_outputs["visual_ocr"] = visual_output
        stage_results.append(visual_stage_result)

        for stage in [EvidenceRiskStage(provider), MethodAnalysisStage(provider)]:
            if is_cancel_requested(cancel_event, cancel_checker):
                return build_cancelled_response(payload, context, previous_outputs, stage_results)

            supplied_output = get_supplied_stage_output(payload, stage.stage_type)
            if supplied_output is not None:
                previous_outputs[stage.stage_type] = supplied_output
                stage_results.append(build_reused_stage_result(stage.stage_type))
            else:
                # 每个 stage 只返回内存结果；数据库写入由 Django 侧负责。
                stage_result = stage.execute(context, previous_outputs)
                previous_outputs[stage.stage_type] = stage_result.output
                stage_results.append(stage_result_to_dict(stage_result))

        for stage in [ScoringStage(provider), ReviewDecisionStage(provider)]:
            if is_cancel_requested(cancel_event, cancel_checker):
                return build_cancelled_response(payload, context, previous_outputs, stage_results)

            stage_result = stage.execute(context, previous_outputs)
            previous_outputs[stage.stage_type] = stage_result.output
            stage_results.append(stage_result_to_dict(stage_result))

        return build_success_response(context, previous_outputs, stage_results)
    except Exception as exc:  # noqa: BLE001
        # 单题异常统一转换成结构化失败结果，避免调用方任务进程被异常打断。
        return build_failed_response(payload, exc)


def grade_homework_answer(
    payload: dict[str, Any],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> dict[str, Any]:
    """
    功能：评价单个学生的作业作答，复用 grade_answer 的证据链路但不执行评分。

    参数：
        payload (dict[str, Any]): 与 grade_answer 基本一致的单题入参；max_score 和 rubric 可省略。
        cancel_event (Any | None): 可选取消事件，通常是 threading.Event。
        cancel_checker (CancelChecker | None): 可选取消检查回调，返回 True 表示已取消。

    返回：
        dict[str, Any]: 保留批改公共字段并新增 homework_feedback 的稳定作业评价结果。
    """
    try:
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_homework_cancelled_response(payload)

        context = build_context(payload, require_max_score=False)
        previous_outputs: dict[str, dict[str, Any]] = {}
        stage_results: list[dict[str, Any]] = []
        provider = build_provider()
        review_mode = get_homework_review_mode()

        if is_cancel_requested(cancel_event, cancel_checker):
            return build_homework_cancelled_response(payload, context, previous_outputs, stage_results, review_mode)

        visual_output, visual_stage_result = get_or_run_visual_output(payload, provider, context)
        previous_outputs["visual_ocr"] = visual_output
        stage_results.append(visual_stage_result)

        for stage in [EvidenceRiskStage(provider), MethodAnalysisStage(provider), HomeworkReviewStage(provider)]:
            if is_cancel_requested(cancel_event, cancel_checker):
                return build_homework_cancelled_response(
                    payload,
                    context,
                    previous_outputs,
                    stage_results,
                    review_mode,
                )

            supplied_output = get_supplied_stage_output(payload, stage.stage_type)
            if supplied_output is not None:
                previous_outputs[stage.stage_type] = supplied_output
                stage_results.append(build_reused_stage_result(stage.stage_type))
            else:
                stage_result = stage.execute(context, previous_outputs)
                previous_outputs[stage.stage_type] = stage_result.output
                stage_results.append(stage_result_to_dict(stage_result))

        if review_mode != "off":
            if is_cancel_requested(cancel_event, cancel_checker):
                return build_homework_cancelled_response(
                    payload,
                    context,
                    previous_outputs,
                    stage_results,
                    review_mode,
                )

            review_stage = HomeworkReviewDecisionStage(provider)
            supplied_review_output = get_supplied_stage_output(payload, review_stage.stage_type)
            if supplied_review_output is not None:
                previous_outputs[review_stage.stage_type] = supplied_review_output
                stage_results.append(build_reused_stage_result(review_stage.stage_type))
            else:
                review_result = review_stage.execute(context, previous_outputs)
                previous_outputs[review_stage.stage_type] = review_result.output
                stage_results.append(stage_result_to_dict(review_result))

        # 模型 HTTP 请求无法被协作式取消中断，因此最终返回前再检查一次并丢弃已取消任务的结果。
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_homework_cancelled_response(
                payload,
                context,
                previous_outputs,
                stage_results,
                review_mode,
            )

        return build_homework_success_response(context, previous_outputs, stage_results, review_mode)
    except Exception as exc:  # noqa: BLE001
        return build_homework_failed_response(payload, exc)


def extract_visual_answer(
    payload: dict[str, Any],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> dict[str, Any]:
    """
    功能：只提取单题题目与学生作答的视觉结构，不执行方法分析和评分。

    参数：
        payload (dict[str, Any]): 单题输入，结构与 grade_answer 的基础输入一致。
        cancel_event (Any | None): 可选取消事件。
        cancel_checker (CancelChecker | None): 可选取消检查回调。

    返回：
        dict[str, Any]: 包含 visual_ocr、stage_outputs 和阶段审计信息的稳定结果。
    """
    try:
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_split_cancelled_response(payload, "visual_ocr")

        context = build_context(payload, require_max_score=False)
        provider = build_provider()
        visual_output, stage_result = get_or_run_visual_output(payload, provider, context)
        return {
            "project_id": context["answer_item"]["project_id"],
            "answer_item_id": context["answer_item"]["id"],
            "question_id": context["answer_item"]["question_id"],
            "question": context["question"],
            "student": context["student"],
            "status": "success",
            "visual_ocr": visual_output,
            "stage_outputs": {"visual_ocr": visual_output},
            "stage_results": [stage_result],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return build_split_failed_response(payload, "visual_ocr", exc)


def analyze_answer_method(
    payload: dict[str, Any],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> dict[str, Any]:
    """
    功能：分析单份作答采用的方法，不执行评分和复核决策。

    参数：
        payload (dict[str, Any]): 单题输入；可通过 visual_ocr 或 stage_outputs.visual_ocr 复用视觉结果。
        cancel_event (Any | None): 可选取消事件。
        cancel_checker (CancelChecker | None): 可选取消检查回调。

    返回：
        dict[str, Any]: 方法摘要、方法置信度、证据片段及完整阶段输出。
    """
    try:
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_split_cancelled_response(payload, "method_analysis")

        context = build_context(payload, require_max_score=False)
        provider = build_provider()
        previous_outputs: dict[str, dict[str, Any]] = {}
        stage_results: list[dict[str, Any]] = []

        visual_output, visual_stage_result = get_or_run_visual_output(payload, provider, context)
        previous_outputs["visual_ocr"] = visual_output
        stage_results.append(visual_stage_result)

        if is_cancel_requested(cancel_event, cancel_checker):
            return build_split_cancelled_response(payload, "method_analysis", previous_outputs, stage_results)

        evidence_output = get_supplied_stage_output(payload, "evidence_risk")
        if evidence_output is not None:
            previous_outputs["evidence_risk"] = evidence_output
            stage_results.append(build_reused_stage_result("evidence_risk"))
        else:
            evidence_result = EvidenceRiskStage(provider).execute(context, previous_outputs)
            previous_outputs["evidence_risk"] = evidence_result.output
            stage_results.append(stage_result_to_dict(evidence_result))

        if is_cancel_requested(cancel_event, cancel_checker):
            return build_split_cancelled_response(payload, "method_analysis", previous_outputs, stage_results)

        method_output = get_supplied_stage_output(payload, "method_analysis")
        if method_output is not None:
            previous_outputs["method_analysis"] = method_output
            stage_results.append(build_reused_stage_result("method_analysis"))
        else:
            method_result = MethodAnalysisStage(provider).execute(context, previous_outputs)
            method_output = method_result.output
            previous_outputs["method_analysis"] = method_output
            stage_results.append(stage_result_to_dict(method_result))

        return {
            "project_id": context["answer_item"]["project_id"],
            "answer_item_id": context["answer_item"]["id"],
            "question_id": context["answer_item"]["question_id"],
            "question": context["question"],
            "student": context["student"],
            "status": "success",
            "method_summary": method_output["method_summary"],
            "method_confidence": method_output["method_confidence"],
            "method_evidence_blocks": method_output["method_evidence_blocks"],
            "method_analysis": method_output,
            "stage_outputs": previous_outputs,
            "stage_results": stage_results,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return build_split_failed_response(payload, "method_analysis", exc)


def grade_answers(
    payloads: list[dict[str, Any]],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> list[dict[str, Any]]:
    """
    功能：顺序批改多道题，主要用于本地联调和外包验收样例。

    参数：
        payloads (list[dict[str, Any]]): 多道题的批改入参列表。
        cancel_event (Any | None): 可选取消事件，会透传给每个单题批改。
        cancel_checker (CancelChecker | None): 可选取消检查回调，会透传给每个单题批改。

    返回：
        list[dict[str, Any]]: 与入参顺序一致的批改结果列表。
    """
    # 正式生产并发应由 Django、Celery 或任务队列控制，这里保持顺序执行以降低集成边界复杂度。
    return [grade_answer(payload, cancel_event=cancel_event, cancel_checker=cancel_checker) for payload in payloads]


def grade_homework_answers(
    payloads: list[dict[str, Any]],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> list[dict[str, Any]]:
    """
    功能：顺序评价多道作业题，保持与 grade_answers 相同的本地联调边界。

    参数：
        payloads (list[dict[str, Any]]): 多道作业题的单题入参列表。
        cancel_event (Any | None): 可选取消事件，会透传给每道题。
        cancel_checker (CancelChecker | None): 可选取消检查回调，会透传给每道题。

    返回：
        list[dict[str, Any]]: 与入参顺序一致的作业评价结果列表。
    """
    return [
        grade_homework_answer(payload, cancel_event=cancel_event, cancel_checker=cancel_checker)
        for payload in payloads
    ]


def discover_method_clusters(
    payload: dict[str, Any],
    cancel_event: Any | None = None,
    cancel_checker: CancelChecker | None = None,
) -> dict[str, Any]:
    """
    功能：基于一批已批改答案的解法摘要，发现高频方法聚类和评分细则缺口。

    参数：
        payload (dict[str, Any]): 方法聚类入参，包含题目信息、当前评分规则和单题解法摘要池。
        cancel_event (Any | None): 可选取消事件，通常是 threading.Event。
        cancel_checker (CancelChecker | None): 可选取消检查回调，返回 True 表示已取消。

    返回：
        dict[str, Any]: 方法聚类结果；成功时包含 method_clusters、gap_candidates、excluded_candidates。
    """
    try:
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_method_cluster_cancelled_response(payload)

        context = build_method_cluster_context(payload)
        provider = build_provider()
        provider_response = provider.run_rubric_gap_discovery(context)

        # 单次模型 HTTP 请求无法被协作式取消中断，因此返回后再次检查并丢弃已取消任务的结果。
        if is_cancel_requested(cancel_event, cancel_checker):
            return build_method_cluster_cancelled_response(payload, context)

        output = unwrap_simple_provider_output(provider_response)
        return {
            "status": "success",
            "project_id": context["project_id"],
            "question_id": str(context["question"].get("id") or ""),
            "current_rubric": context["rubric"],
            "method_clusters": output.get("method_clusters", []),
            "gap_candidates": output.get("gap_candidates", []),
            "excluded_candidates": output.get("excluded_candidates", []),
            "raw_output": output,
            "model_metadata": {
                "model_name": get_stage_model_name(provider, "rubric_gap_discovery"),
                "stage_type": "rubric_gap_discovery",
                "raw_output_path": getattr(provider_response, "raw_output_path", ""),
            },
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "project_id": str(payload.get("project_id") or "") if isinstance(payload, dict) else "",
            "question_id": get_payload_question_id(payload),
            "current_rubric": get_input_current_rubric(payload),
            "method_clusters": [],
            "gap_candidates": [],
            "excluded_candidates": [],
            "raw_output": {},
            "model_metadata": {},
            "error": {
                "code": exc.__class__.__name__,
                "message": str(exc),
            },
        }


def build_method_cluster_cancelled_response(
    payload: Any,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    功能：构造方法聚合接口的稳定取消响应，并保留题目和旧评分规则标识。

    参数：
        payload (Any): 原始方法聚合入参。
        context (dict[str, Any] | None): 已完成规范化的聚合上下文；调用前取消时为空。

    返回：
        dict[str, Any]: status 为 cancelled 的方法聚合结果。
    """
    project_id = str(payload.get("project_id") or "") if isinstance(payload, dict) else ""
    question_id = get_payload_question_id(payload)
    current_rubric = get_input_current_rubric(payload)
    if context is not None:
        project_id = context["project_id"]
        question_id = str(context["question"].get("id") or "")
        current_rubric = context["rubric"]

    return {
        "status": "cancelled",
        "cancelled": True,
        "project_id": project_id,
        "question_id": question_id,
        "current_rubric": current_rubric,
        "method_clusters": [],
        "gap_candidates": [],
        "excluded_candidates": [],
        "raw_output": {},
        "model_metadata": {},
        "error": {
            "code": "CANCELLED",
            "message": "方法聚合已取消",
        },
    }


def build_provider() -> Any:
    """
    功能：根据环境变量构建模型 provider。

    参数：
        无

    返回：
        Any: mock provider 或 OpenAI-compatible provider。
    """
    provider_config = load_provider_config()
    if provider_config.provider_name == "openai_compatible":
        return OpenAICompatibleProvider(provider_config)
    return MockModelProvider()


def build_method_cluster_context(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把外部方法聚类入参转换为原 rubric gap discovery 可消费的上下文结构。

    参数：
        payload (dict[str, Any]): 外部调用方传入的方法聚类 JSON。

    返回：
        dict[str, Any]: 包含 question、rubric、summary_pool 和 threshold 的上下文。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 JSON 对象")

    question = normalize_question(require_dict(payload, "question"))
    summary_pool = payload.get("method_pool", payload.get("summary_pool"))
    if not isinstance(summary_pool, list):
        raise ValueError("method_pool 或 summary_pool 必须是数组")
    summary_pool = normalize_method_pool(summary_pool)

    rubric = payload.get("current_rubric", question.get("rubric", {}))
    threshold = payload.get("threshold") if isinstance(payload.get("threshold"), dict) else {}
    return {
        "id": str(payload.get("run_id") or "external_rubric_gap_run"),
        "project_id": str(payload.get("project_id") or "external_project"),
        "question": question,
        "rubric": rubric,
        "summary_pool": summary_pool,
        "threshold": threshold,
    }


def build_context(payload: dict[str, Any], require_max_score: bool = True) -> dict[str, Any]:
    """
    功能：把外部 JSON 入参转换为原流水线 stage 可消费的上下文结构。

    参数：
        payload (dict[str, Any]): 外部调用方传入的原始单题 JSON。
        require_max_score (bool): 是否要求题目提供满分；考试批改为 True，作业评价为 False。

    返回：
        dict[str, Any]: 包含 answer_item、question、student、answer_image 的统一上下文。
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 JSON 对象")

    question = require_dict(payload, "question")
    student_answer_value = payload.get("student_answer")
    student_answer = student_answer_value if isinstance(student_answer_value, dict) else {}
    student = payload.get("student") if isinstance(payload.get("student"), dict) else {}

    answer_item_id = str(payload.get("answer_item_id") or "").strip()
    if not answer_item_id:
        raise ValueError("answer_item_id 不能为空")

    question_no = str(question.get("question_no") or payload.get("question_no") or "").strip()
    if not question_no:
        raise ValueError("question.question_no 不能为空")

    rubric = question.get("rubric") if isinstance(question.get("rubric"), dict) else {}
    raw_question_type = rubric.get("type") or question.get("question_type") or question.get("type")
    if not str(raw_question_type or "").strip():
        # v2.6 继续把题型作为正式批改配置的一部分，避免缺失后所有题目静默回退到通用规则。
        raise ValueError("question.question_type 不能为空")

    max_score = question.get("max_score")
    if require_max_score and max_score is None:
        raise ValueError("question.max_score 不能为空")
    normalized_max_score = float(max_score) if max_score is not None else None

    image_path = str(student_answer.get("image_path") or "").strip()
    image_url = str(student_answer.get("image_url") or "").strip()
    ocr_text = str(student_answer.get("ocr_text") or "").strip()
    text = str(student_answer.get("text") or "").strip()
    has_supplied_visual = get_raw_supplied_stage_output(payload, "visual_ocr") is not None
    if not image_path and not image_url and not ocr_text and not text and not has_supplied_visual:
        raise ValueError("student_answer.image_path、image_url、ocr_text、text 至少提供一个")

    normalized_question = normalize_question(question)
    answer_item = {
        "id": answer_item_id,
        "project_id": str(payload.get("project_id") or "external_project"),
        "question_id": str(normalized_question.get("id") or normalized_question.get("question_id") or ""),
        "question_no": question_no,
        "student_no": str(student.get("student_no") or student.get("student_id") or ""),
        "student_name": str(student.get("name") or student.get("student_name") or ""),
        "max_score": normalized_max_score,
    }

    return {
        "answer_item": answer_item,
        "question": normalized_question,
        "student": {
            "student_no": answer_item["student_no"],
            "student_name": answer_item["student_name"],
        },
        "answer_image": {"file_path": image_path, "image_url": image_url} if image_path or image_url else None,
        "student_answer": {
            "image_path": image_path,
            "image_url": image_url,
            "ocr_text": ocr_text,
            "text": text,
        },
        "pipeline_run": {
            "id": str(payload.get("pipeline_run_id") or "external_run"),
            "project_id": answer_item["project_id"],
            "task_id": str(payload.get("task_id") or "external_task"),
        },
    }


def normalize_question(question: dict[str, Any]) -> dict[str, Any]:
    """
    功能：整理题目信息字段，让外部入参和原流水线字段尽量兼容。

    参数：
        question (dict[str, Any]): 外部传入的题目信息。

    返回：
        dict[str, Any]: 规范化后的题目信息。
    """
    normalized_question = dict(question)
    normalized_question.setdefault("id", question.get("question_id", ""))
    normalized_question.setdefault("rubric", question.get("rubric", {}))
    normalized_question.setdefault("question_text", question.get("stem", ""))
    normalized_question.setdefault("standard_answer", question.get("standard_answer", ""))
    return normalized_question


def get_or_run_visual_output(
    payload: dict[str, Any],
    provider: Any,
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    功能：优先复用调用方提供的视觉结果，否则执行视觉阶段。

    参数：
        payload (dict[str, Any]): 外部调用入参。
        provider (Any): 当前模型 provider。
        context (dict[str, Any]): 单题运行上下文。

    返回：
        tuple[dict[str, Any], dict[str, Any]]: 视觉输出和阶段审计摘要。
    """
    supplied_output = get_supplied_stage_output(payload, "visual_ocr")
    if supplied_output is not None:
        return supplied_output, build_reused_stage_result("visual_ocr")
    return build_or_run_visual_output(provider, context)


def get_raw_supplied_stage_output(payload: Any, stage_type: str) -> Any | None:
    """读取顶层或 stage_outputs 中由调用方提供的阶段结果。"""
    if not isinstance(payload, dict):
        return None
    if stage_type in payload:
        return payload[stage_type]
    stage_outputs = payload.get("stage_outputs")
    if isinstance(stage_outputs, dict) and stage_type in stage_outputs:
        return stage_outputs[stage_type]
    return None


def get_supplied_stage_output(payload: Any, stage_type: str) -> dict[str, Any] | None:
    """复制并校验调用方提供的阶段结果，避免修改原始入参。"""
    supplied_output = get_raw_supplied_stage_output(payload, stage_type)
    if supplied_output is None:
        return None
    if not isinstance(supplied_output, dict):
        raise ValueError(f"{stage_type} 必须是 JSON 对象")
    return validate_stage_output(stage_type, deepcopy(supplied_output))


def build_reused_stage_result(stage_type: str) -> dict[str, Any]:
    """为复用的阶段结果生成明确的审计标记。"""
    return {
        "stage_type": stage_type,
        "status": "success",
        "model_name": "",
        "prompt_name": stage_type,
        "prompt_version": "reused",
        "raw_output_path": "",
        "error_message": "",
        "source_type": "supplied_stage_output",
    }


def normalize_method_pool(method_pool: list[Any]) -> list[dict[str, Any]]:
    """兼容方法摘要条目和 analyze_answer_method 的完整返回结果。"""
    normalized_pool: list[dict[str, Any]] = []
    for index, item in enumerate(method_pool):
        if not isinstance(item, dict):
            raise ValueError(f"method_pool[{index}] 必须是 JSON 对象")

        normalized_item = dict(item)
        method_output = item.get("method_analysis")
        if not isinstance(method_output, dict):
            stage_outputs = item.get("stage_outputs")
            if isinstance(stage_outputs, dict) and isinstance(stage_outputs.get("method_analysis"), dict):
                method_output = stage_outputs["method_analysis"]

        if isinstance(method_output, dict):
            normalized_item.setdefault("method_summary", method_output.get("method_summary", ""))
            normalized_item.setdefault("method_confidence", method_output.get("method_confidence", ""))

        method_summary = normalized_item.get("method_summary")
        if not isinstance(method_summary, str) or not method_summary.strip():
            raise ValueError(f"method_pool[{index}].method_summary 不能为空")
        normalized_pool.append(normalized_item)
    return normalized_pool


def get_input_current_rubric(payload: Any) -> Any:
    """在失败响应中也尽量原样回传调用方提供的当前评分规则。"""
    if not isinstance(payload, dict):
        return {}
    if "current_rubric" in payload:
        return payload["current_rubric"]
    question = payload.get("question")
    if isinstance(question, dict):
        return question.get("rubric", {})
    return {}


def build_or_run_visual_output(provider: Any, context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    功能：根据学生作答来源生成 visual_ocr 输出和阶段摘要。

    参数：
        provider (Any): 当前模型 provider。
        context (dict[str, Any]): 单题批改上下文。

    返回：
        tuple[dict[str, Any], dict[str, Any]]: visual_ocr 输出和 stage 摘要。
    """
    if context["answer_image"] is not None:
        # 有本地图片或图片 URL 时沿用视觉 OCR，再把结果交给后续评分阶段。
        stage_result = VisualOcrStage(provider).execute(context, {})
        return stage_result.output, stage_result_to_dict(stage_result)

    student_answer = context["student_answer"]
    answer_text = student_answer["ocr_text"] or student_answer["text"]
    source_type = "external_ocr_text" if student_answer["ocr_text"] else "plain_text"
    output = {
        "transcription": {
            "source_type": source_type,
            "student_answer_text": answer_text,
        },
        "normalized_input": {
            "answer_item_id": context["answer_item"]["id"],
            "question_no": context["answer_item"]["question_no"],
            "image_path": "",
            "image_url": "",
            "student_answer_text": answer_text,
            "final_answer_candidates": [],
        },
        "ocr_risk": {
            "level": "medium" if source_type == "external_ocr_text" else "low",
            "types": ["external_ocr_without_image"] if source_type == "external_ocr_text" else ["text_only_without_image"],
            "observations": [],
        },
    }
    output = validate_stage_output("visual_ocr", output)
    return output, build_virtual_stage_result("visual_ocr", output, get_stage_model_name(provider, "visual_ocr"))


def build_success_response(
    context: dict[str, Any],
    previous_outputs: dict[str, dict[str, Any]],
    stage_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    功能：把各 stage 输出压平成外包最关心的单题批改返回结构。

    参数：
        context (dict[str, Any]): 单题批改上下文。
        previous_outputs (dict[str, dict[str, Any]]): 各 stage 的结构化输出。
        stage_results (list[dict[str, Any]]): 各 stage 的审计信息。

    返回：
        dict[str, Any]: 单题批改成功结果。
    """
    scoring_output = previous_outputs["scoring"]
    review_output = previous_outputs["review_decision"]
    suggested_score = float(scoring_output["suggested_score"])
    review_advice = review_output["review_advice"]

    return {
        "answer_item_id": context["answer_item"]["id"],
        "status": "success",
        "suggested_score": suggested_score,
        "max_score": float(scoring_output["max_score"]),
        "final_score": suggested_score if review_advice == "auto_accept" else None,
        "question_type": get_question_type(context["question"]),
        "selected_path_id": scoring_output.get("selected_path_id"),
        "summary": scoring_output.get("summary", ""),
        "score_points": scoring_output.get("score_points", []),
        "deductions": scoring_output.get("deductions", []),
        "confidence_level": review_output.get("confidence_level", "medium"),
        "review_advice": review_advice,
        "risk_tags": review_output.get("risk_tags", []),
        "review_focus": review_output.get("review_focus", []),
        "evidence_safe": review_output.get("evidence_safe", False),
        "stage_outputs": previous_outputs,
        "stage_results": stage_results,
        "error": None,
    }


def build_homework_success_response(
    context: dict[str, Any],
    previous_outputs: dict[str, dict[str, Any]],
    stage_results: list[dict[str, Any]],
    review_mode: str,
) -> dict[str, Any]:
    """
    功能：把作业评价阶段输出压平成与 grade_answer 基本兼容的顶层结构。

    参数：
        context (dict[str, Any]): 单题作业评价上下文。
        previous_outputs (dict[str, dict[str, Any]]): 已完成阶段的结构化输出。
        stage_results (list[dict[str, Any]]): 阶段审计摘要。
        review_mode (str): 独立复核模式，取值为 off、shadow 或 visible。

    返回：
        dict[str, Any]: 不含分数、包含 homework_feedback 的成功结果。
    """
    homework_output = previous_outputs["homework_review"]
    decision_output = previous_outputs.get("homework_review_decision")
    effective_review_output = (
        decision_output if review_mode == "visible" and isinstance(decision_output, dict) else homework_output
    )
    stage_outputs: dict[str, Any] = dict(previous_outputs)
    stage_outputs.setdefault("homework_review_decision", None)
    homework_feedback = {
        "answer_status": homework_output.get("answer_status", "unclear"),
        "summary": homework_output.get("summary", ""),
        "strengths": homework_output.get("strengths", []),
        "issues": homework_output.get("issues", []),
        "improvement_suggestions": homework_output.get("improvement_suggestions", []),
        "evidence_usage": homework_output.get("evidence_usage", {}),
    }

    return {
        "answer_item_id": context["answer_item"]["id"],
        "status": "success",
        "grading_mode": "homework",
        "suggested_score": None,
        "max_score": context["answer_item"].get("max_score"),
        "final_score": None,
        "question_type": get_question_type(context["question"]),
        "selected_path_id": None,
        "summary": homework_output.get("summary", ""),
        "score_points": [],
        "deductions": [],
        "homework_feedback": homework_feedback,
        "confidence_level": effective_review_output.get("confidence_level", "medium"),
        "review_advice": effective_review_output.get("review_advice", "sample_review"),
        "risk_tags": effective_review_output.get("risk_tags", []),
        "review_focus": effective_review_output.get("review_focus", []),
        "evidence_safe": effective_review_output.get("evidence_safe", False),
        "homework_review_mode": review_mode,
        "homework_review_status": "not_run" if review_mode == "off" else review_mode,
        "stage_outputs": stage_outputs,
        "stage_results": stage_results,
        "error": None,
    }


def build_empty_homework_feedback() -> dict[str, Any]:
    """
    功能：构造失败或取消场景使用的稳定空作业评价对象。

    参数：
        无

    返回：
        dict[str, Any]: 字段完整但不包含业务评价的空对象。
    """
    return {
        "answer_status": "unclear",
        "summary": "",
        "strengths": [],
        "issues": [],
        "improvement_suggestions": [],
        "evidence_usage": {},
    }


def build_homework_failed_response(payload: Any, exc: Exception) -> dict[str, Any]:
    """
    功能：构造作业评价统一失败响应，保持与考试批改相同的错误处理方式。

    参数：
        payload (Any): 原始作业单题入参。
        exc (Exception): 捕获到的异常对象。

    返回：
        dict[str, Any]: status 为 failed 的稳定作业评价结果。
    """
    answer_item_id = payload.get("answer_item_id", "") if isinstance(payload, dict) else ""
    return {
        "answer_item_id": str(answer_item_id),
        "status": "failed",
        "grading_mode": "homework",
        "suggested_score": None,
        "max_score": None,
        "final_score": None,
        "selected_path_id": None,
        "summary": "",
        "score_points": [],
        "deductions": [],
        "homework_feedback": build_empty_homework_feedback(),
        "confidence_level": "low",
        "review_advice": "required_review",
        "risk_tags": ["homework_grading_failed"],
        "review_focus": ["查看作业评价错误信息，并人工核对该题。"],
        "evidence_safe": False,
        "homework_review_mode": "unavailable",
        "homework_review_status": "failed",
        "stage_outputs": {},
        "stage_results": [],
        "error": {
            "code": exc.__class__.__name__,
            "message": str(exc),
        },
    }


def build_failed_response(payload: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """
    功能：构造统一失败响应，方便 Django 侧按固定字段入库或记录日志。

    参数：
        payload (dict[str, Any]): 原始入参。
        exc (Exception): 捕获到的异常对象。

    返回：
        dict[str, Any]: 单题批改失败结果。
    """
    answer_item_id = payload.get("answer_item_id", "") if isinstance(payload, dict) else ""
    return {
        "answer_item_id": str(answer_item_id),
        "status": "failed",
        "suggested_score": None,
        "max_score": None,
        "final_score": None,
        "selected_path_id": None,
        "summary": "",
        "score_points": [],
        "deductions": [],
        "confidence_level": "low",
        "review_advice": "required_review",
        "risk_tags": ["ai_grading_failed"],
        "review_focus": ["查看 AI 批改错误信息，并人工复核该题。"],
        "evidence_safe": False,
        "stage_outputs": {},
        "stage_results": [],
        "error": {
            "code": exc.__class__.__name__,
            "message": str(exc),
        },
    }


def build_split_failed_response(payload: Any, stage_type: str, exc: Exception) -> dict[str, Any]:
    """构造视觉或方法分析接口的统一失败响应。"""
    answer_item_id = payload.get("answer_item_id", "") if isinstance(payload, dict) else ""
    return {
        "answer_item_id": str(answer_item_id),
        "status": "failed",
        stage_type: {},
        "stage_outputs": {},
        "stage_results": [],
        "error": {
            "code": exc.__class__.__name__,
            "message": str(exc),
        },
    }


def build_split_cancelled_response(
    payload: Any,
    stage_type: str,
    previous_outputs: dict[str, dict[str, Any]] | None = None,
    stage_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造视觉或方法分析接口的统一取消响应。"""
    answer_item_id = payload.get("answer_item_id", "") if isinstance(payload, dict) else ""
    return {
        "answer_item_id": str(answer_item_id),
        "status": "cancelled",
        "cancelled": True,
        stage_type: {},
        "stage_outputs": previous_outputs or {},
        "stage_results": stage_results or [],
        "error": {
            "code": "CANCELLED",
            "message": "调用已取消",
        },
    }


def build_cancelled_response(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
    previous_outputs: dict[str, dict[str, Any]] | None = None,
    stage_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    功能：构造统一取消响应，让调用方可以明确丢弃该题结果。

    参数：
        payload (dict[str, Any]): 原始单题入参。
        context (dict[str, Any] | None): 已构造的运行上下文。
        previous_outputs (dict[str, dict[str, Any]] | None): 取消前已经完成的阶段输出。
        stage_results (list[dict[str, Any]] | None): 取消前已经完成的阶段摘要。

    返回：
        dict[str, Any]: status 为 cancelled 的单题响应。
    """
    answer_item_id = ""
    if context is not None:
        answer_item_id = str(context.get("answer_item", {}).get("id") or "")
    if not answer_item_id and isinstance(payload, dict):
        answer_item_id = str(payload.get("answer_item_id") or "")

    return {
        "answer_item_id": answer_item_id,
        "status": "cancelled",
        "cancelled": True,
        "suggested_score": None,
        "max_score": None,
        "final_score": None,
        "selected_path_id": None,
        "summary": "",
        "score_points": [],
        "deductions": [],
        "confidence_level": "low",
        "review_advice": "required_review",
        "risk_tags": ["ai_grading_cancelled"],
        "review_focus": [],
        "evidence_safe": False,
        "stage_outputs": previous_outputs or {},
        "stage_results": stage_results or [],
        "error": {
            "code": "CANCELLED",
            "message": "AI 批改已取消",
        },
    }


def build_homework_cancelled_response(
    payload: Any,
    context: dict[str, Any] | None = None,
    previous_outputs: dict[str, dict[str, Any]] | None = None,
    stage_results: list[dict[str, Any]] | None = None,
    review_mode: str = "off",
) -> dict[str, Any]:
    """
    功能：构造作业评价取消响应，并保留取消前已经完成的阶段结果。

    参数：
        payload (Any): 原始作业入参。
        context (dict[str, Any] | None): 已构造的运行上下文。
        previous_outputs (dict[str, dict[str, Any]] | None): 取消前已经完成的阶段输出。
        stage_results (list[dict[str, Any]] | None): 取消前已经完成的阶段摘要。
        review_mode (str): 当前独立复核模式。

    返回：
        dict[str, Any]: status 为 cancelled 的稳定作业评价结果。
    """
    answer_item_id = ""
    if context is not None:
        answer_item_id = str(context.get("answer_item", {}).get("id") or "")
    if not answer_item_id and isinstance(payload, dict):
        answer_item_id = str(payload.get("answer_item_id") or "")

    stage_outputs: dict[str, Any] = dict(previous_outputs or {})
    stage_outputs.setdefault("homework_review_decision", None)
    return {
        "answer_item_id": answer_item_id,
        "status": "cancelled",
        "cancelled": True,
        "grading_mode": "homework",
        "suggested_score": None,
        "max_score": None,
        "final_score": None,
        "selected_path_id": None,
        "summary": "",
        "score_points": [],
        "deductions": [],
        "homework_feedback": build_empty_homework_feedback(),
        "confidence_level": "low",
        "review_advice": "required_review",
        "risk_tags": ["homework_grading_cancelled"],
        "review_focus": [],
        "evidence_safe": False,
        "homework_review_mode": review_mode,
        "homework_review_status": "cancelled",
        "stage_outputs": stage_outputs,
        "stage_results": stage_results or [],
        "error": {
            "code": "CANCELLED",
            "message": "作业评价已取消",
        },
    }


def is_cancel_requested(cancel_event: Any | None = None, cancel_checker: CancelChecker | None = None) -> bool:
    """
    功能：统一检查外部调用方是否已经请求取消当前批改。

    参数：
        cancel_event (Any | None): 可选的 threading.Event 风格对象，支持 is_set 方法。
        cancel_checker (CancelChecker | None): 可选回调函数，返回 True 表示已取消。

    返回：
        bool: 已取消时返回 True，否则返回 False。
    """
    if cancel_event is not None:
        is_set = getattr(cancel_event, "is_set", None)
        if callable(is_set) and bool(is_set()):
            return True

    if cancel_checker is not None:
        if not callable(cancel_checker):
            raise ValueError("cancel_checker 必须是可调用对象")
        return bool(cancel_checker())

    return False


def stage_result_to_dict(stage_result: Any) -> dict[str, Any]:
    """
    功能：把 StageExecutionResult 转成普通字典，避免调用方依赖内部 dataclass。

    参数：
        stage_result (Any): 原流水线 stage 返回的执行结果。

    返回：
        dict[str, Any]: 可 JSON 序列化的 stage 结果摘要。
    """
    return {
        "stage_type": stage_result.stage_type,
        "status": stage_result.status,
        "model_name": stage_result.model_name,
        "prompt_name": stage_result.prompt_name,
        "prompt_version": stage_result.prompt_version,
        "raw_output_path": stage_result.raw_output_path,
        "error_message": stage_result.error_message,
    }


def build_virtual_stage_result(stage_type: str, output: dict[str, Any], model_name: str) -> dict[str, Any]:
    """
    功能：为手工适配的 visual_ocr 输出生成 stage 审计摘要。

    参数：
        stage_type (str): 阶段类型。
        output (dict[str, Any]): 阶段输出。
        model_name (str): 模型名称。

    返回：
        dict[str, Any]: 可返回给调用方的阶段摘要。
    """
    source_type = output.get("transcription", {}).get("source_type", "model_visual_ocr")
    return {
        "stage_type": stage_type,
        "status": "success",
        "model_name": model_name,
        "prompt_name": stage_type,
        "prompt_version": "v2",
        "raw_output_path": "",
        "error_message": "",
        "source_type": source_type,
    }


def get_stage_model_name(provider: Any, stage_type: str) -> str:
    """
    功能：读取指定 stage 实际使用的模型名称。

    参数：
        provider (Any): 当前模型 provider。
        stage_type (str): 阶段类型。

    返回：
        str: 模型名称。
    """
    resolver = getattr(provider, "get_stage_model_name", None)
    if callable(resolver):
        return str(resolver(stage_type))
    return str(getattr(provider, "model_name", ""))


def unwrap_simple_provider_output(provider_response: Any) -> dict[str, Any]:
    """
    功能：提取 provider 的结构化输出，兼容 mock 字典和真实 ProviderResponse。

    参数：
        provider_response (Any): provider 返回值。

    返回：
        dict[str, Any]: 结构化模型输出。
    """
    output = getattr(provider_response, "output", provider_response)
    if not isinstance(output, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return output


def get_payload_question_id(payload: Any) -> str:
    """
    功能：从失败场景的原始入参中尽量提取 question_id。

    参数：
        payload (Any): 原始入参。

    返回：
        str: 题目 ID；无法提取时返回空字符串。
    """
    if not isinstance(payload, dict):
        return ""
    question = payload.get("question")
    if not isinstance(question, dict):
        return ""
    return str(question.get("question_id") or question.get("id") or "")


def require_dict(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    """
    功能：读取必填对象字段，并在类型错误时给出明确错误。

    参数：
        payload (dict[str, Any]): 原始 JSON 对象。
        field_name (str): 字段名称。

    返回：
        dict[str, Any]: 对应字段值。
    """
    value = payload.get(field_name)
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 JSON 对象")
    return value
