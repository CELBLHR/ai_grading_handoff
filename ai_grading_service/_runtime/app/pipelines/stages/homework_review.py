from __future__ import annotations

from typing import Any

from app.pipelines.contracts import validate_stage_output
from app.pipelines.question_type import get_question_type
from app.pipelines.stages.base import PipelineStage, StageExecutionResult
from app.providers.base import unwrap_provider_response


class HomeworkReviewStage(PipelineStage):
    """根据学生可见作答生成不含分数的作业评价。"""

    stage_type = "homework_review"
    prompt_name = "homework_review"
    prompt_version = "v2.5"

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        """
        功能：组合视觉、证据风险和方法分析结果，调用作业评价模型。

        参数：
            context (dict[str, Any]): 当前单题和学生作答上下文。
            previous_outputs (dict[str, dict[str, Any]]): 已完成的前三个证据阶段输出。

        返回：
            StageExecutionResult: 经过契约校验的作业评价阶段结果。
        """
        visual_output = previous_outputs["visual_ocr"]
        evidence_risk_output = previous_outputs["evidence_risk"]
        method_analysis_output = previous_outputs["method_analysis"]
        stage_input = self.build_input(context, previous_outputs)
        stage_input["question"] = context["question"]
        stage_input["question_type"] = get_question_type(context["question"])
        stage_input["visual_output"] = visual_output
        stage_input["evidence_risk_output"] = evidence_risk_output
        stage_input["method_analysis_output"] = method_analysis_output
        provider_response = self.provider.run_homework_review(
            context,
            visual_output,
            evidence_risk_output,
            method_analysis_output,
        )
        output, raw_output_path, model_name = unwrap_provider_response(self.provider, provider_response)
        output = validate_stage_output(self.stage_type, output)
        return self.success_result(stage_input, output, raw_output_path=raw_output_path, model_name=model_name)
