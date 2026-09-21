from __future__ import annotations

from typing import Any

from app.pipelines.contracts import validate_stage_output
from app.pipelines.question_type import get_question_type
from app.pipelines.stages.base import PipelineStage, StageExecutionResult
from app.providers.base import unwrap_provider_response


class ScoringStage(PipelineStage):
    stage_type = "scoring"
    prompt_name = "scoring"
    prompt_version = "v2.3"

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        visual_output = previous_outputs["visual_ocr"]
        evidence_risk_output = previous_outputs["evidence_risk"]
        method_analysis_output = previous_outputs["method_analysis"]
        stage_input = self.build_input(context, previous_outputs)
        stage_input["question"] = context["question"]
        stage_input["question_type"] = get_question_type(context["question"])
        stage_input["visual_output"] = visual_output
        stage_input["evidence_risk_output"] = evidence_risk_output
        stage_input["method_analysis_output"] = method_analysis_output
        provider_response = self.provider.run_scoring(
            context,
            visual_output,
            evidence_risk_output,
            method_analysis_output,
        )
        output, raw_output_path, model_name = unwrap_provider_response(self.provider, provider_response)
        output = validate_stage_output(self.stage_type, output)
        validate_scoring_path_selection(context["question"], output)
        active_scoring_points = get_active_scoring_points(context["question"], output)
        validate_score_points_against_rubric(active_scoring_points, output)
        validate_binary_score_points(active_scoring_points, output)
        return self.success_result(stage_input, output, raw_output_path=raw_output_path, model_name=model_name)


def validate_scoring_path_selection(question: dict[str, Any], scoring_output: dict[str, Any]) -> None:
    """校验模型选择的评分路径是否来自输入 rubric。"""
    scoring_paths = get_input_scoring_paths(question)
    if not scoring_paths:
        scoring_output.setdefault("selected_path_id", None)
        return

    if "selected_path_id" not in scoring_output:
        raise ValueError("scoring.selected_path_id 不能为空；存在 scoring_paths 时必须明确选择路径或返回 null")

    path_map = build_scoring_path_map(scoring_paths)
    selected_path_id = scoring_output.get("selected_path_id")
    if selected_path_id is None or not str(selected_path_id).strip():
        scoring_output["selected_path_id"] = None
        if scoring_output.get("score_points"):
            raise ValueError("未匹配评分路径时 scoring.score_points 必须为空数组")
        if abs(parse_score(scoring_output.get("suggested_score"), "scoring.suggested_score")) >= 1e-9:
            raise ValueError("未匹配评分路径时 scoring.suggested_score 必须为 0")
        return

    selected_path_id = str(selected_path_id).strip()
    if selected_path_id not in path_map:
        raise ValueError(f"scoring.selected_path_id 未在 rubric.scoring_paths 中声明：{selected_path_id}")
    scoring_output["selected_path_id"] = selected_path_id


def get_active_scoring_points(
    question: dict[str, Any],
    scoring_output: dict[str, Any],
) -> list[dict[str, Any]]:
    """返回当前选中路径或旧版平铺 rubric 的评分点。"""
    scoring_paths = get_input_scoring_paths(question)
    if not scoring_paths:
        return get_input_scoring_points(question)

    selected_path_id = scoring_output.get("selected_path_id")
    if selected_path_id is None:
        return []
    return get_path_scoring_points(build_scoring_path_map(scoring_paths)[str(selected_path_id)])


def validate_score_points_against_rubric(
    scoring_points: list[dict[str, Any]],
    scoring_output: dict[str, Any],
) -> None:
    """
    功能：校验模型输出的得分点是否严格来自输入 rubric。

    参数：
        scoring_points (list[dict[str, Any]]): 当前生效路径或旧版平铺 rubric 的评分点。
        scoring_output (dict[str, Any]): scoring 阶段已经通过基础结构校验的模型输出。

    返回：
        None: 校验通过时不返回额外数据；校验失败时抛出 ValueError。
    """
    # 外部没有提供评分点时，无法建立 id 白名单，因此保持兼容旧输入格式。
    if not scoring_points:
        return

    # 使用字符串集合比较，是为了兼容外部系统把 id 传成数字或字符串的情况。
    allowed_point_ids = {
        str(point.get("id")).strip()
        for point in scoring_points
        if isinstance(point, dict) and str(point.get("id", "")).strip()
    }
    if not allowed_point_ids:
        return

    output_points = scoring_output.get("score_points", [])
    invalid_point_ids: list[str] = []
    missing_point_ids = set(allowed_point_ids)
    for point in output_points:
        if not isinstance(point, dict):
            raise ValueError("scoring.score_points 中的每一项都必须是 JSON 对象")

        # 对接输出里历史上同时出现过 point_id 和 id，这里两者都支持，但值必须来自输入 id。
        output_point_id = str(point.get("point_id", point.get("id", ""))).strip()
        if output_point_id not in allowed_point_ids:
            invalid_point_ids.append(output_point_id or "<empty>")
            continue
        missing_point_ids.discard(output_point_id)

    if invalid_point_ids:
        raise ValueError(
            "scoring.score_points 包含未在当前生效评分点中声明的 point_id："
            + ", ".join(sorted(set(invalid_point_ids)))
        )

    if missing_point_ids:
        raise ValueError(
            "scoring.score_points 缺少当前生效评分点中的 point_id："
            + ", ".join(sorted(missing_point_ids))
        )


def get_input_scoring_points(question: dict[str, Any]) -> list[dict[str, Any]]:
    """
    功能：从题目信息中提取当前生效的输入评分点列表。

    参数：
        question (dict[str, Any]): 当前题目信息，通常包含 rubric.scoring_points。

    返回：
        list[dict[str, Any]]: 输入评分点列表；不存在或格式不正确时返回空列表。
    """
    rubric = question.get("rubric")
    if not isinstance(rubric, dict):
        return []

    scoring_points = rubric.get("scoring_points")
    if not isinstance(scoring_points, list):
        return []

    # 只返回对象类型的评分点，避免脏数据影响后续 id 白名单构造。
    return [point for point in scoring_points if isinstance(point, dict)]


def get_input_scoring_paths(question: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 rubric.scoring_paths；字段不存在时返回空列表。"""
    rubric = question.get("rubric")
    if not isinstance(rubric, dict):
        return []
    scoring_paths = rubric.get("scoring_paths")
    if scoring_paths is None:
        return []
    if not isinstance(scoring_paths, list):
        raise ValueError("rubric.scoring_paths 必须是数组")
    for index, path in enumerate(scoring_paths):
        if not isinstance(path, dict):
            raise ValueError(f"rubric.scoring_paths[{index}] 必须是 JSON 对象")
    return scoring_paths


def build_scoring_path_map(scoring_paths: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """校验评分路径 ID 和路径内评分点，并建立路径索引。"""
    path_map: dict[str, dict[str, Any]] = {}
    for index, path in enumerate(scoring_paths):
        path_id = str(path.get("id", "")).strip()
        if not path_id:
            raise ValueError(f"rubric.scoring_paths[{index}].id 不能为空")
        if path_id in path_map:
            raise ValueError(f"rubric.scoring_paths 存在重复 id：{path_id}")

        raw_points = path.get("points")
        if not isinstance(raw_points, list) or any(not isinstance(point, dict) for point in raw_points):
            raise ValueError(f"rubric.scoring_paths[{path_id}].points 必须是 JSON 对象数组")
        points = get_path_scoring_points(path)
        point_ids = [str(point.get("id", "")).strip() for point in points]
        if not points or any(not point_id for point_id in point_ids):
            raise ValueError(f"rubric.scoring_paths[{path_id}].points 必须包含带 id 的评分点")
        if len(point_ids) != len(set(point_ids)):
            raise ValueError(f"rubric.scoring_paths[{path_id}].points 存在重复 id")
        path_map[path_id] = path
    return path_map


def get_path_scoring_points(path: dict[str, Any]) -> list[dict[str, Any]]:
    """读取一条评分路径中的有效评分点对象。"""
    points = path.get("points")
    if not isinstance(points, list):
        return []
    return [point for point in points if isinstance(point, dict)]


def validate_binary_score_points(
    scoring_points: list[dict[str, Any]],
    scoring_output: dict[str, Any],
) -> None:
    """
    功能：校验每个输入评分点是否只按“0 分或满分”二值给分。

    参数：
        scoring_points (list[dict[str, Any]]): 当前生效路径或旧版平铺 rubric 的评分点。
        scoring_output (dict[str, Any]): scoring 阶段已经通过基础结构校验和评分点 id 校验的模型输出。

    返回：
        None: 校验通过时不返回额外数据；校验失败时抛出 ValueError。
    """
    point_score_map = build_input_point_score_map(scoring_points)
    if not point_score_map:
        return

    output_points = scoring_output.get("score_points", [])
    total_score = 0.0
    invalid_scores: list[str] = []
    for point in output_points:
        output_point_id = str(point.get("point_id", point.get("id", ""))).strip()
        awarded_score = parse_score(point.get("score"), f"scoring.score_points[{output_point_id}].score")
        max_point_score = point_score_map.get(output_point_id)
        if max_point_score is None:
            continue

        # 使用极小容差比较，是为了避免模型或 JSON 解析带来的浮点表示误差。
        is_zero_score = abs(awarded_score) < 1e-9
        is_full_score = abs(awarded_score - max_point_score) < 1e-9
        if not is_zero_score and not is_full_score:
            invalid_scores.append(f"{output_point_id}: {awarded_score}，允许值为 0 或 {max_point_score}")
        total_score += awarded_score

    if invalid_scores:
        raise ValueError("scoring.score_points 存在非二值评分：" + "; ".join(invalid_scores))

    suggested_score = parse_score(scoring_output.get("suggested_score"), "scoring.suggested_score")
    if abs(suggested_score - total_score) >= 1e-9:
        raise ValueError(
            f"scoring.suggested_score 必须等于各评分点得分之和："
            f"suggested_score={suggested_score}, score_points_sum={total_score}"
        )


def build_input_point_score_map(scoring_points: list[dict[str, Any]]) -> dict[str, float]:
    """
    功能：构造输入评分点 id 到该点评分满分的映射。

    参数：
        scoring_points (list[dict[str, Any]]): 当前生效的评分点列表。

    返回：
        dict[str, float]: 评分点 id 到满分的映射；缺少有效评分点时返回空字典。
    """
    point_score_map: dict[str, float] = {}
    for point in scoring_points:
        point_id = str(point.get("id", "")).strip()
        if not point_id:
            continue
        point_score_map[point_id] = parse_score(point.get("score"), f"rubric.scoring_points[{point_id}].score")
    return point_score_map


def parse_score(value: Any, field_name: str) -> float:
    """
    功能：把评分字段转换为浮点数，并在转换失败时给出明确字段名。

    参数：
        value (Any): 待转换的评分值。
        field_name (str): 当前评分字段名，用于错误提示。

    返回：
        float: 转换后的评分值。
    """
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数字") from exc
