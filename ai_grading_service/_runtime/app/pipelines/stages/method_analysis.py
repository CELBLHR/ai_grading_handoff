from __future__ import annotations

from typing import Any

from app.pipelines.contracts import validate_stage_output
from app.pipelines.stages.base import PipelineStage, StageExecutionResult
from app.providers.base import unwrap_provider_response


class MethodAnalysisStage(PipelineStage):
    stage_type = "method_analysis"
    prompt_name = "method_analysis"
    prompt_version = "v2"

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        visual_output = previous_outputs["visual_ocr"]
        evidence_risk_output = previous_outputs["evidence_risk"]
        stage_input = self.build_input(context, previous_outputs)
        stage_input["question"] = context["question"]
        stage_input["visual_output"] = visual_output
        stage_input["evidence_risk_output"] = evidence_risk_output
        provider_response = self.provider.run_method_analysis(context, visual_output, evidence_risk_output)
        output, raw_output_path, model_name = unwrap_provider_response(self.provider, provider_response)
        output = validate_stage_output(self.stage_type, output)
        return self.success_result(stage_input, output, raw_output_path=raw_output_path, model_name=model_name)
