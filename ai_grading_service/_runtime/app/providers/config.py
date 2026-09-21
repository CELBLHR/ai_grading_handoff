from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_env_int, get_env_value


ALLOWED_HOMEWORK_REVIEW_MODES = {"off", "shadow", "visible"}


@dataclass(frozen=True)
class ProviderEndpointConfig:
    """
    功能：保存某一类模型调用端点的配置。

    参数：
        dataclass: 自动生成初始化方法的数据类装饰器。

    返回：
        ProviderEndpointConfig: 包含 base_url、model_name、api_key 等字段的配置对象。
    """

    api_key: str
    base_url: str
    model_name: str
    timeout_seconds: int
    retry_count: int
    temperature: float
    max_tokens: int
    reasoning_effort: str
    thinking_type: str


@dataclass(frozen=True)
class PipelineProviderConfig:
    """
    功能：保存 AI 初评流水线 provider 的整体配置。

    参数：
        dataclass: 自动生成初始化方法的数据类装饰器。

    返回：
        PipelineProviderConfig: 包含 provider 类型和各阶段端点配置的对象。
    """

    provider_name: str
    visual: ProviderEndpointConfig
    grading: ProviderEndpointConfig
    gate: ProviderEndpointConfig


def get_env_float(name: str, default: float) -> float:
    """
    功能：读取浮点型环境变量，读取失败时回退到默认值。

    参数：
        name (str): 环境变量名称。
        default (float): 环境变量不存在或格式错误时的默认值。

    返回：
        float: 可直接用于 temperature 等模型参数的浮点数。
    """
    raw_value = get_env_value(name, "")
    if not raw_value:
        return default

    try:
        return float(raw_value)
    except ValueError:
        # 本地配置写错时不让服务启动失败，真实调用阶段会通过输出结果暴露问题。
        return default


def get_homework_review_mode() -> str:
    """
    功能：读取作业评价独立复核模式，并拒绝容易造成误解的非法配置。

    参数：
        无

    返回：
        str: `off`、`shadow` 或 `visible` 中的一个值。
    """
    review_mode = (get_env_value("HOMEWORK_REVIEW_MODE", "off") or "off").strip().lower()
    if review_mode not in ALLOWED_HOMEWORK_REVIEW_MODES:
        allowed_modes = ", ".join(sorted(ALLOWED_HOMEWORK_REVIEW_MODES))
        raise ValueError(f"HOMEWORK_REVIEW_MODE 非法：{review_mode}；允许值为 {allowed_modes}")
    return review_mode


def load_provider_config() -> PipelineProviderConfig:
    """
    功能：从环境变量读取 provider 配置，并提供本地演示默认值。

    参数：
        无

    返回：
        PipelineProviderConfig: 可供 registry 构造 provider 的配置对象。
    """
    provider_name = (get_env_value("AI_PIPELINE_PROVIDER", "mock") or "mock").lower()

    return PipelineProviderConfig(
        provider_name=provider_name,
        visual=ProviderEndpointConfig(
            api_key=get_env_value("VISUAL_MODEL_API_KEY", "") or get_env_value("ARK_API_KEY", ""),
            base_url=get_env_value("VISUAL_MODEL_BASE_URL", "") or "https://ark.cn-beijing.volces.com/api/v3",
            model_name=get_env_value("VISUAL_MODEL_NAME", "") or "doubao-seed-2-0-lite-260215",
            timeout_seconds=get_env_int("VISUAL_MODEL_TIMEOUT_SECONDS", 180),
            retry_count=get_env_int("VISUAL_MODEL_RETRY_COUNT", 2),
            temperature=get_env_float("VISUAL_MODEL_TEMPERATURE", 0.0),
            max_tokens=get_env_int("VISUAL_MODEL_MAX_TOKENS", 131072),
            reasoning_effort=get_env_value("VISUAL_MODEL_REASONING_EFFORT", ""),
            thinking_type=get_env_value("VISUAL_MODEL_THINKING_TYPE", "disabled"),
        ),
        grading=ProviderEndpointConfig(
            api_key=get_env_value("GRADING_MODEL_API_KEY", "") or get_env_value("DEEPSEEK_API_KEY", ""),
            base_url=get_env_value("GRADING_MODEL_BASE_URL", "") or "https://api.deepseek.com",
            model_name=get_env_value("GRADING_MODEL_NAME", "") or "deepseek-v4-flash",
            timeout_seconds=get_env_int("GRADING_MODEL_TIMEOUT_SECONDS", 120),
            retry_count=get_env_int("GRADING_MODEL_RETRY_COUNT", 2),
            temperature=get_env_float("GRADING_MODEL_TEMPERATURE", 0.0),
            max_tokens=get_env_int("GRADING_MODEL_MAX_TOKENS", 131072),
            reasoning_effort=get_env_value("GRADING_MODEL_REASONING_EFFORT", "high"),
            thinking_type=get_env_value("GRADING_MODEL_THINKING_TYPE", "enabled"),
        ),
        gate=ProviderEndpointConfig(
            api_key=get_env_value("GATE_MODEL_API_KEY", "")
            or get_env_value("GRADING_MODEL_API_KEY", "")
            or get_env_value("DEEPSEEK_API_KEY", ""),
            base_url=get_env_value("GATE_MODEL_BASE_URL", "")
            or get_env_value("GRADING_MODEL_BASE_URL", "")
            or "https://api.deepseek.com",
            model_name=get_env_value("GATE_MODEL_NAME", "")
            or get_env_value("GRADING_MODEL_NAME", "")
            or "deepseek-v4-flash",
            timeout_seconds=get_env_int("GATE_MODEL_TIMEOUT_SECONDS", get_env_int("GRADING_MODEL_TIMEOUT_SECONDS", 120)),
            retry_count=get_env_int("GATE_MODEL_RETRY_COUNT", get_env_int("GRADING_MODEL_RETRY_COUNT", 2)),
            temperature=get_env_float("GATE_MODEL_TEMPERATURE", get_env_float("GRADING_MODEL_TEMPERATURE", 0.0)),
            max_tokens=get_env_int("GATE_MODEL_MAX_TOKENS", 32768),
            reasoning_effort=get_env_value("GATE_MODEL_REASONING_EFFORT", get_env_value("GRADING_MODEL_REASONING_EFFORT", "high")),
            thinking_type=get_env_value("GATE_MODEL_THINKING_TYPE", get_env_value("GRADING_MODEL_THINKING_TYPE", "enabled")),
        ),
    )
