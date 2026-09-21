from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResponse:
    """
    功能：封装真实模型 provider 的结构化输出和原始审计信息。

    参数：
        dataclass: 自动生成初始化方法的数据类装饰器。

    返回：
        ProviderResponse: 可被 stage 转换为 StageExecutionResult 的 provider 响应。
    """

    output: dict[str, Any]
    raw_text: str = ""
    raw_output_path: str = ""
    model_name: str = ""


class ModelProvider(Protocol):
    """
    功能：定义模型 provider 的最小协议，mock 和真实 provider 都必须实现该协议。

    参数：
        Protocol: Python 结构化类型基类。

    返回：
        ModelProvider: provider 协议类型。
    """

    model_name: str

    def run_question_image_parser(self, context: dict[str, Any]) -> dict[str, Any] | ProviderResponse:
        """
        功能：识别题目图片中已经存在的题干、答案、分数和评分标注。

        参数：
            context (dict[str, Any]): 题目生成上下文，包含图片来源和业务类型。

        返回：
            dict[str, Any] | ProviderResponse: 图片题目识别结果。
        """

    def run_question_config_generator(self, context: dict[str, Any]) -> dict[str, Any] | ProviderResponse:
        """
        功能：根据规范化题干补齐标准答案和评分路径等缺失配置。

        参数：
            context (dict[str, Any]): 题目生成上下文，包含允许生成的字段清单。

        返回：
            dict[str, Any] | ProviderResponse: AI 生成的题目配置内容。
        """

    def run_visual_ocr(self, context: dict[str, Any]) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行视觉 OCR 阶段。

        参数：
            context (dict[str, Any]): 单个答题项的统一上下文。

        返回：
            dict[str, Any] | ProviderResponse: 视觉阶段输出。
        """

    def run_evidence_risk(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行证据风险检测阶段。
        """

    def run_method_analysis(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行解法分析阶段。
        """

    def run_scoring(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行 V2 评分阶段。
        """

    def run_review_decision(
        self,
        context: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        scoring_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行 V2 复核决策阶段。
        """

    def run_homework_review(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行不含分数的作业评价阶段。
        """

    def run_homework_review_decision(
        self,
        context: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        homework_review_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行作业评价可靠性复核阶段。
        """

    def run_rubric_gap_discovery(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any] | ProviderResponse:
        """
        功能：执行题目级评分细则缺口发现。
        """


def get_provider_stage_model_name(provider: ModelProvider, stage_type: str) -> str:
    """
    功能：按阶段读取 provider 实际使用的模型名称。

    参数：
        provider (ModelProvider): 当前流水线阶段持有的模型 provider。
        stage_type (str): 当前失败或成功的阶段类型。

    返回：
        str: 当前阶段应写入审计记录的模型名称。
    """
    resolver = getattr(provider, "get_stage_model_name", None)
    if callable(resolver):
        # 真实 provider 里视觉、批改、闸门可能使用不同模型；失败阶段也必须记录正确模型。
        return str(resolver(stage_type))

    # mock provider 只有一个统一模型名，保留原有兼容逻辑。
    return provider.model_name


def unwrap_provider_response(
    provider: ModelProvider,
    response: dict[str, Any] | ProviderResponse,
) -> tuple[dict[str, Any], str, str]:
    """
    功能：把 mock provider 的普通字典和真实 provider 的 ProviderResponse 统一拆成 stage 所需字段。

    参数：
        provider (ModelProvider): 当前 stage 使用的模型 provider。
        response (dict[str, Any] | ProviderResponse): provider 返回的原始响应。

    返回：
        tuple[dict[str, Any], str, str]: 结构化输出、raw 输出路径、模型名称。
    """
    if isinstance(response, ProviderResponse):
        return response.output, response.raw_output_path, response.model_name or provider.model_name

    return response, "", provider.model_name
