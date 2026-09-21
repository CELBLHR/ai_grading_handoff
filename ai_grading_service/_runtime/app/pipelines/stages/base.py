from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.providers.base import ModelProvider, get_provider_stage_model_name


@dataclass
class StageExecutionResult:
    """
    功能：描述一个流水线阶段的标准执行结果。

    参数：
        dataclass: 自动生成初始化方法的数据类装饰器。

    返回：
        StageExecutionResult: 可写入 pipeline_stage_results 的阶段结果。
    """

    stage_type: str
    status: str
    input: dict[str, Any]
    output: dict[str, Any]
    model_name: str
    prompt_name: str
    prompt_version: str
    error_message: str = ""
    stage_version: str = "v2"
    raw_output_path: str = ""


class PipelineStage:
    """
    功能：定义正式流水线阶段的基础接口。

    参数：
        无。

    返回：
        PipelineStage: 可被 executor 调度的阶段实例。
    """

    stage_type = ""
    stage_version = "v2"
    prompt_name = ""
    prompt_version = "v2"

    def __init__(self, provider: ModelProvider) -> None:
        """
        功能：保存 stage 依赖的模型 provider。

        参数：
            provider (ModelProvider): 模型 provider。

        返回：
            None
        """
        self.provider = provider

    def build_input(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """
        功能：构造阶段输入，默认保留 answer_item 和已完成阶段结果索引。

        参数：
            context (dict[str, Any]): 单个答题项上下文。
            previous_outputs (dict[str, dict[str, Any]]): 前置阶段输出。

        返回：
            dict[str, Any]: 阶段输入。
        """
        return {
            "answer_item_id": context["answer_item"]["id"],
            "question_no": context["answer_item"]["question_no"],
            "previous_stage_types": list(previous_outputs.keys()),
        }

    def execute(self, context: dict[str, Any], previous_outputs: dict[str, dict[str, Any]]) -> StageExecutionResult:
        """
        功能：执行阶段，子类必须覆盖。

        参数：
            context (dict[str, Any]): 单个答题项上下文。
            previous_outputs (dict[str, dict[str, Any]]): 前置阶段输出。

        返回：
            StageExecutionResult: 阶段执行结果。
        """
        raise NotImplementedError("PipelineStage.execute 必须由子类实现")

    def success_result(
        self,
        stage_input: dict[str, Any],
        output: dict[str, Any],
        raw_output_path: str = "",
        model_name: str = "",
    ) -> StageExecutionResult:
        """
        功能：构造成功阶段结果。

        参数：
            stage_input (dict[str, Any]): 阶段输入。
            output (dict[str, Any]): 阶段输出。

        返回：
            StageExecutionResult: 成功状态的标准阶段结果。
        """
        return StageExecutionResult(
            stage_type=self.stage_type,
            stage_version=self.stage_version,
            status="success",
            input=stage_input,
            output=output,
            model_name=model_name or self.provider.model_name,
            prompt_name=self.prompt_name or self.stage_type,
            prompt_version=self.prompt_version,
            raw_output_path=raw_output_path,
        )


def skipped_stage_result(stage_type: str, answer_item_id: str, reason: str) -> StageExecutionResult:
    """
    功能：构造跳过阶段结果，用于记录没有答题图等无法继续处理的情况。

    参数：
        stage_type (str): 阶段类型。
        answer_item_id (str): 答题项 ID。
        reason (str): 跳过原因。

    返回：
        StageExecutionResult: skipped 状态的阶段结果。
    """
    return StageExecutionResult(
        stage_type=stage_type,
        status="skipped",
        input={"answer_item_id": answer_item_id},
        output={},
        model_name="mock-model",
        prompt_name=stage_type,
        prompt_version="v2",
        error_message=reason,
    )


def failed_stage_result(
    stage: PipelineStage,
    stage_input: dict[str, Any],
    error_message: str,
) -> StageExecutionResult:
    """
    功能：构造失败阶段结果，确保异常也能进入审计表。

    参数：
        stage (PipelineStage): 当前执行失败的阶段实例。
        stage_input (dict[str, Any]): 阶段输入。
        error_message (str): 错误信息。

    返回：
        StageExecutionResult: failed 状态的阶段结果。
    """
    return StageExecutionResult(
        stage_type=stage.stage_type,
        stage_version=stage.stage_version,
        status="failed",
        input=stage_input,
        output={},
        model_name=get_provider_stage_model_name(stage.provider, stage.stage_type),
        prompt_name=stage.prompt_name or stage.stage_type,
        prompt_version=stage.prompt_version,
        error_message=error_message,
    )
