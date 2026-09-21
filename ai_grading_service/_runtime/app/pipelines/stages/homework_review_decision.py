from __future__ import annotations

from typing import Any

from app.pipelines.contracts import validate_stage_output
from app.pipelines.question_type import get_question_type
from app.pipelines.stages.base import PipelineStage, StageExecutionResult
from app.providers.base import unwrap_provider_response


class HomeworkReviewDecisionStage(PipelineStage):
    """独立判断作业评价是否有充分卷面证据支持。"""

    stage_type = "homework_review_decision"
    prompt_name = "homework_review_decision"
    prompt_version = "v2.5"

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        """
        功能：复核作业评价的证据可靠性，但不改写作业评价正文。

        参数：
            context (dict[str, Any]): 当前单题和学生作答上下文。
            previous_outputs (dict[str, dict[str, Any]]): 包含作业评价在内的前置阶段输出。

        返回：
            StageExecutionResult: 经过复核决策契约校验的阶段结果。
        """
        evidence_risk_output = previous_outputs["evidence_risk"]
        method_analysis_output = previous_outputs["method_analysis"]
        homework_review_output = previous_outputs["homework_review"]
        stage_input = self.build_input(context, previous_outputs)
        stage_input["question_type"] = get_question_type(context["question"])
        stage_input["evidence_risk_output"] = evidence_risk_output
        stage_input["method_analysis_output"] = method_analysis_output
        stage_input["homework_review_output"] = homework_review_output
        provider_response = self.provider.run_homework_review_decision(
            context,
            evidence_risk_output,
            homework_review_output,
            method_analysis_output,
        )
        output, raw_output_path, model_name = unwrap_provider_response(self.provider, provider_response)
        output = validate_stage_output(self.stage_type, output)
        return self.success_result(stage_input, output, raw_output_path=raw_output_path, model_name=model_name)
