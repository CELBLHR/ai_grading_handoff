from __future__ import annotations

from typing import Any

from app.pipelines.contracts import validate_stage_output
from app.pipelines.question_type import get_question_type
from app.pipelines.stages.base import PipelineStage, StageExecutionResult
from app.providers.base import unwrap_provider_response


class ReviewDecisionStage(PipelineStage):
    stage_type = "review_decision"
    prompt_name = "review_decision"
    prompt_version = "v2"

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        evidence_risk_output = previous_outputs["evidence_risk"]
        method_analysis_output = previous_outputs["method_analysis"]
        scoring_output = previous_outputs["scoring"]
        stage_input = self.build_input(context, previous_outputs)
        stage_input["question_type"] = get_question_type(context["question"])
        stage_input["evidence_risk_output"] = evidence_risk_output
        stage_input["method_analysis_output"] = method_analysis_output
        stage_input["scoring_output"] = scoring_output
        provider_response = self.provider.run_review_decision(
            context,
            evidence_risk_output,
            scoring_output,
            method_analysis_output,
        )
        output, raw_output_path, model_name = unwrap_provider_response(self.provider, provider_response)
        output = validate_stage_output(self.stage_type, output)
        output = enforce_unmatched_scoring_path_review(context["question"], scoring_output, output)
        return self.success_result(stage_input, output, raw_output_path=raw_output_path, model_name=model_name)


def enforce_unmatched_scoring_path_review(
    question: dict[str, Any],
    scoring_output: dict[str, Any],
    review_output: dict[str, Any],
) -> dict[str, Any]:
    """存在多路径 rubric 但模型未匹配路径时，强制转人工复核。"""
    rubric = question.get("rubric")
    scoring_paths = rubric.get("scoring_paths") if isinstance(rubric, dict) else None
    if not isinstance(scoring_paths, list) or not scoring_paths:
        return review_output
    if scoring_output.get("selected_path_id") is not None:
        return review_output

    normalized_output = dict(review_output)
    normalized_output["confidence_level"] = "low"
    normalized_output["review_advice"] = "required_review"
    normalized_output["evidence_safe"] = False
    append_unique(normalized_output, "risk_tags", "unmatched_scoring_path")
    append_unique(normalized_output, "review_focus", "学生解法未能可靠匹配当前任何评分路径，请人工确认评分规则覆盖情况。")
    append_unique(normalized_output, "policy_overrides", "unmatched_scoring_path_requires_review")
    return normalized_output


def append_unique(output: dict[str, Any], field_name: str, value: str) -> None:
    """向数组字段追加不重复的策略标记。"""
    values = output.get(field_name)
    if not isinstance(values, list):
        values = []
        output[field_name] = values
    if value not in values:
        values.append(value)
