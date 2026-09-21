from __future__ import annotations

from typing import Any

from app.pipelines.contracts import validate_stage_output
from app.pipelines.stages.base import PipelineStage, StageExecutionResult
from app.providers.base import unwrap_provider_response


class VisualOcrStage(PipelineStage):
    """
    功能：执行视觉/OCR 阶段。

    参数：
        PipelineStage: stage 基类。

    返回：
        VisualOcrStage: 视觉阶段实例。
    """

    stage_type = "visual_ocr"
    prompt_name = "visual_ocr"

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        """
        功能：调用 provider 生成视觉/OCR 输出。

        参数：
            context (dict[str, Any]): 单个答题项上下文。
            previous_outputs (dict[str, dict[str, Any]]): 前置阶段输出，本阶段通常为空。

        返回：
            StageExecutionResult: 标准阶段结果。
        """
        stage_input = self.build_input(context, previous_outputs)
        image_path = context["answer_image"].get("file_path", "")
        image_url = context["answer_image"].get("image_url", "")
        stage_input["image_path"] = image_path
        stage_input["image_url"] = image_url
        provider_response = self.provider.run_visual_ocr(context)
        output, raw_output_path, model_name = unwrap_provider_response(self.provider, provider_response)
        output = validate_stage_output(self.stage_type, output)
        return self.success_result(stage_input, output, raw_output_path=raw_output_path, model_name=model_name)
