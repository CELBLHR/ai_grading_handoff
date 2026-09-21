from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.core.config import get_config, get_env_value
from app.pipelines.question_type import get_question_type
from app.prompts.loader import load_homework_review_prompt, load_prompt, load_scoring_prompt
from app.providers.base import ProviderResponse
from app.providers.config import PipelineProviderConfig, ProviderEndpointConfig
from app.providers.json_utils import parse_model_json


class OpenAICompatibleProvider:
    """
    功能：提供 OpenAI 兼容接口的 provider 骨架，供后续真实模型接入复用。

    参数：
        config (PipelineProviderConfig): provider 环境配置。

    返回：
        OpenAICompatibleProvider: 可被流水线 stage 调用的 provider 实例。
    """

    def __init__(self, config: PipelineProviderConfig) -> None:
        """
        功能：保存 provider 配置并加载固定版本 prompt。

        参数：
            config (PipelineProviderConfig): provider 环境配置。

        返回：
            None
        """
        self.config = config
        self.model_name = config.grading.model_name
        self.prompts = {
            "question_image_parser": load_prompt("question_image_parser"),
            "question_config_generator": load_prompt("question_config_generator"),
            "question_output_formatter": load_prompt("question_output_formatter"),
            "visual_ocr": load_prompt("visual_ocr"),
            "evidence_risk": load_prompt("evidence_risk"),
            "method_analysis": load_prompt("method_analysis"),
            "review_decision": load_prompt("review_decision"),
            "homework_review_decision": load_prompt("homework_review_decision"),
            "rubric_gap_discovery": load_prompt("rubric_gap_discovery"),
        }

    def get_stage_model_name(self, stage_type: str) -> str:
        """
        功能：返回指定阶段真实调用的模型名称。

        参数：
            stage_type (str): 流水线阶段类型。

        返回：
            str: 对应阶段的模型名称。
        """
        # 失败阶段无法从 ProviderResponse 读取模型名，因此这里按阶段配置补齐审计元数据。
        if stage_type in {"visual_ocr", "question_image_parser"}:
            return self.config.visual.model_name
        if stage_type in {"review_decision", "homework_review_decision"}:
            return self.config.gate.model_name
        return self.config.grading.model_name

    def run_question_image_parser(self, context: dict[str, Any]) -> ProviderResponse:
        """
        功能：调用视觉模型识别题目图片，但不生成图片中不存在的答案或评分点。

        参数：
            context (dict[str, Any]): 包含 answer_image、grading_mode 和请求标识的题目生成上下文。

        返回：
            ProviderResponse: 结构化图片识别结果及模型审计信息。
        """
        image_source_url = build_image_source_url(context["answer_image"])
        messages = [
            {
                "role": "system",
                "content": "你是教育题目图片结构化识别专家。只识别可见事实，并严格输出合法 JSON。",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "prompt": self.prompts["question_image_parser"],
                                "grading_mode": context["grading_mode"],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": image_source_url}},
                ],
            },
        ]
        return self.call_stage_json(
            context=context,
            stage_type="question_image_parser",
            endpoint=self.config.visual,
            messages=messages,
        )

    def run_question_config_generator(self, context: dict[str, Any]) -> ProviderResponse:
        """
        功能：调用文本模型，仅补齐题目配置中被明确列为缺失的内容。

        参数：
            context (dict[str, Any]): 包含题干、分数、已有内容和 allowed_generation_fields 的上下文。

        返回：
            ProviderResponse: 结构化题目配置生成结果及模型审计信息。
        """
        user_payload = {
            "prompt": self.prompts["question_config_generator"],
            "grading_mode": context["grading_mode"],
            "question": context["question"],
            "existing_content": context["existing_content"],
            "allowed_generation_fields": context["allowed_generation_fields"],
        }
        messages = [
            {
                "role": "system",
                "content": "你是数学题目标准答案与评分规则设计专家。只补齐允许生成的字段，并严格输出合法 JSON。",
            },
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="question_config_generator",
            endpoint=self.config.grading,
            messages=messages,
        )

    def run_question_output_formatter(self, context: dict[str, Any]) -> ProviderResponse:
        """
        功能：对题目配置最终响应执行一次受限格式修复，不重新求解题目。

        参数：
            context (dict[str, Any]): 包含 original_output 和 validation_issues 的修复上下文。

        返回：
            ProviderResponse: 修复后的完整题目配置及模型审计信息。
        """
        user_payload = {
            "prompt": self.prompts["question_output_formatter"],
            "validation_issues": context["validation_issues"],
            "original_output": context["original_output"],
        }
        messages = [
            {
                "role": "system",
                "content": "你是数学题目配置格式质量修复器。只修复格式，不改变任何数学含义或业务结构。",
            },
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="question_output_formatter",
            endpoint=self.config.grading,
            messages=messages,
        )

    def run_visual_ocr(self, context: dict[str, Any]) -> ProviderResponse:
        """
        功能：调用视觉模型，把切题图转换为结构化 OCR/视觉结果。

        参数：
            context (dict[str, Any]): 单个 answer_item 的统一上下文。

        返回：
            ProviderResponse: 包含结构化视觉输出和 raw 输出路径的响应。
        """
        image_source_url = build_image_source_url(context["answer_image"])
        messages = [
            {"role": "system", "content": "你是严格输出 JSON 的阅卷视觉解析助手。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self.prompts["visual_ocr"]},
                    {"type": "image_url", "image_url": {"url": image_source_url}},
                ],
            },
        ]
        return self.call_stage_json(
            context=context,
            stage_type="visual_ocr",
            endpoint=self.config.visual,
            messages=messages,
        )

    def run_evidence_risk(self, context: dict[str, Any], visual_output: dict[str, Any]) -> ProviderResponse:
        user_payload = {
            "prompt": self.prompts["evidence_risk"],
            "question": context["question"],
            "student": context["student"],
            "answer_item": context["answer_item"],
            "visual_output": visual_output,
        }
        messages = [
            {"role": "system", "content": "你是严格输出 JSON 的证据风险检测器。"},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="evidence_risk",
            endpoint=self.config.grading,
            messages=messages,
        )

    def run_method_analysis(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
    ) -> ProviderResponse:
        user_payload = {
            "prompt": self.prompts["method_analysis"],
            "question": context["question"],
            "student": context["student"],
            "answer_item": context["answer_item"],
            "visual_output": visual_output,
            "evidence_risk_output": evidence_risk_output,
        }
        messages = [
            {"role": "system", "content": "你是严格输出 JSON 的解法分析助手。"},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="method_analysis",
            endpoint=self.config.grading,
            messages=messages,
        )

    def run_scoring(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> ProviderResponse:
        """
        功能：调用批改模型，根据题目、评分规则和视觉结果生成 AI 建议分。

        参数：
            context (dict[str, Any]): 单个 answer_item 的统一上下文。
            visual_output (dict[str, Any]): 视觉阶段输出。

        返回：
            ProviderResponse: 包含结构化批改输出和 raw 输出路径的响应。
        """
        question_type = get_question_type(context["question"])
        user_payload = {
            "prompt": load_scoring_prompt(question_type),
            "question_type": question_type,
            "question": context["question"],
            "student": context["student"],
            "answer_item": context["answer_item"],
            "visual_output": visual_output,
            "evidence_risk_output": evidence_risk_output,
            "method_analysis_output": method_analysis_output,
        }
        messages = [
            {"role": "system", "content": "你是严格按评分规则输出 JSON 的 AI 阅卷员。"},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="scoring",
            endpoint=self.config.grading,
            messages=messages,
        )

    def run_review_decision(
        self,
        context: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        scoring_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> ProviderResponse:
        """
        功能：调用闸门模型，判断证据是否足以支持当前 AI 建议分。

        参数：
            context (dict[str, Any]): 单个 answer_item 的统一上下文。
            visual_output (dict[str, Any]): 视觉阶段输出。
            grading_output (dict[str, Any]): 批改阶段输出。

        返回：
            ProviderResponse: 包含结构化闸门输出和 raw 输出路径的响应。
        """
        user_payload = {
            "prompt": self.prompts["review_decision"],
            "question_type": get_question_type(context["question"]),
            "question": context["question"],
            "student": context["student"],
            "answer_item": context["answer_item"],
            "evidence_risk_output": evidence_risk_output,
            "method_analysis_output": method_analysis_output,
            "scoring_output": scoring_output,
        }
        messages = [
            {"role": "system", "content": "你是严格输出 JSON 的复核决策助手。"},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="review_decision",
            endpoint=self.config.gate,
            messages=messages,
        )

    def run_homework_review(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> ProviderResponse:
        """
        功能：调用批改模型生成不含分数和评分点的作业评价。

        参数：
            context (dict[str, Any]): 当前单题上下文。
            visual_output (dict[str, Any]): 视觉或文本规范化结果。
            evidence_risk_output (dict[str, Any]): 证据风险结果。
            method_analysis_output (dict[str, Any]): 学生解法分析结果。

        返回：
            ProviderResponse: 作业评价结构化输出和审计信息。
        """
        question_type = get_question_type(context["question"])
        user_payload = {
            "prompt": load_homework_review_prompt(question_type),
            "question_type": question_type,
            "question": context["question"],
            "student": context["student"],
            "answer_item": context["answer_item"],
            "visual_output": visual_output,
            "evidence_risk_output": evidence_risk_output,
            "method_analysis_output": method_analysis_output,
        }
        messages = [
            {
                "role": "system",
                "content": "你是面向学生作业订正的数学作业批改教师。你不打分、不生成评分点，只输出合法 JSON。",
            },
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        first_response = self.call_stage_json(
            context=context,
            stage_type="homework_review",
            endpoint=self.config.grading,
            messages=messages,
        )
        latex_issues = find_homework_review_latex_issues(first_response.output)
        if not latex_issues:
            return first_response

        repair_instruction = {
            "task": "修正上一份作业评价 JSON 中的公式格式，并重新输出完整合法 JSON。",
            "requirements": [
                "只修正公式的 LaTeX 包裹、转写和 JSON 反斜杠转义。",
                "不得改变 answer_status、问题数量、问题类型、可信度或复核建议。",
                "所有数学表达式必须位于 $...$ 或 $$...$$ 中。",
                "无法可靠还原的公式改写为中文说明，禁止保留 OCR 噪声。",
                "不要输出 Markdown 代码块或 JSON 之外的说明。",
            ],
            "validation_issues": latex_issues,
        }
        repair_messages = [
            *messages,
            {"role": "assistant", "content": first_response.raw_text},
            {"role": "user", "content": json.dumps(repair_instruction, ensure_ascii=False)},
        ]
        repaired_response = self.call_stage_json(
            context=context,
            stage_type="homework_review",
            endpoint=self.config.grading,
            messages=repair_messages,
        )
        remaining_issues = find_homework_review_latex_issues(repaired_response.output)
        if remaining_issues:
            issue_summary = "；".join(remaining_issues[:10])
            raise ValueError(f"作业评价结果中的公式仍不满足 LaTeX 要求：{issue_summary}")
        if build_homework_review_structure_signature(
            first_response.output
        ) != build_homework_review_structure_signature(repaired_response.output):
            raise ValueError("作业评价模型在纠正公式格式时改变了评价结构")

        return repaired_response

    def run_homework_review_decision(
        self,
        context: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        homework_review_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> ProviderResponse:
        """
        功能：调用闸门模型判断作业评价是否有充分卷面证据支持。

        参数：
            context (dict[str, Any]): 当前单题上下文。
            evidence_risk_output (dict[str, Any]): 证据风险结果。
            homework_review_output (dict[str, Any]): 作业评价结果。
            method_analysis_output (dict[str, Any]): 学生解法分析结果。

        返回：
            ProviderResponse: 作业评价独立复核结果和审计信息。
        """
        user_payload = {
            "prompt": self.prompts["homework_review_decision"],
            "question_type": get_question_type(context["question"]),
            "question": context["question"],
            "student": context["student"],
            "answer_item": context["answer_item"],
            "evidence_risk_output": evidence_risk_output,
            "method_analysis_output": method_analysis_output,
            "homework_review_output": homework_review_output,
        }
        messages = [
            {
                "role": "system",
                "content": "你是作业评价可靠性复核助手。你不修改评价，只判断证据是否足以支持评价，并只输出合法 JSON。",
            },
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        return self.call_stage_json(
            context=context,
            stage_type="homework_review_decision",
            endpoint=self.config.gate,
            messages=messages,
        )

    def run_rubric_gap_discovery(self, context: dict[str, Any]) -> ProviderResponse:
        """
        功能：执行方法聚合，并在聚合报告包含明显裸公式时要求模型纠正一次。

        参数：
            context (dict[str, Any]): 包含题目、当前 rubric 和同题方法摘要池的聚合上下文。

        返回：
            ProviderResponse: 已通过方法聚合 LaTeX 质量检查的结构化输出。
        """
        user_payload = {
            "prompt": self.prompts["rubric_gap_discovery"],
            "question": context["question"],
            "current_rubric": context["rubric"],
            "summary_pool": context["summary_pool"],
            "threshold": context.get("threshold", {}),
        }
        messages = [
            {"role": "system", "content": "你是严格输出 JSON 的评分细则缺口发现助手。"},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        first_response = self.call_stage_json(
            context=context,
            stage_type="rubric_gap_discovery",
            endpoint=self.config.grading,
            messages=messages,
        )
        latex_issues = find_rubric_gap_latex_issues(first_response.output)
        if not latex_issues:
            return first_response

        # 首次结果有明显裸公式时，让同一模型仅修正展示格式，避免程序猜测数学语义。
        repair_instruction = {
            "task": "修正上一份 JSON 中的公式格式，并重新输出完整合法 JSON。",
            "requirements": [
                "只修正公式的 LaTeX 包裹、转写和 JSON 反斜杠转义，不改变聚类、候选、计数、评分路径、分值或 ID。",
                "所有数学表达式必须位于 $...$ 或 $$...$$ 中。",
                "无法可靠还原的公式改写为中文说明，禁止保留 OCR 噪声。",
                "不要输出 Markdown 代码块或 JSON 之外的说明。",
            ],
            "validation_issues": latex_issues,
        }
        repair_messages = [
            *messages,
            {"role": "assistant", "content": first_response.raw_text},
            {"role": "user", "content": json.dumps(repair_instruction, ensure_ascii=False)},
        ]
        repaired_response = self.call_stage_json(
            context=context,
            stage_type="rubric_gap_discovery",
            endpoint=self.config.grading,
            messages=repair_messages,
        )
        remaining_issues = find_rubric_gap_latex_issues(repaired_response.output)
        if remaining_issues:
            issue_summary = "；".join(remaining_issues[:10])
            raise ValueError(f"方法聚合结果中的公式仍不满足 LaTeX 要求：{issue_summary}")
        if build_rubric_gap_structure_signature(first_response.output) != build_rubric_gap_structure_signature(
            repaired_response.output
        ):
            raise ValueError("方法聚合模型在纠正公式格式时改变了聚类或评分路径结构")

        return repaired_response

    def call_stage_json(
        self,
        context: dict[str, Any],
        stage_type: str,
        endpoint: ProviderEndpointConfig,
        messages: list[dict[str, Any]],
    ) -> ProviderResponse:
        """
        功能：调用 OpenAI 兼容 chat/completions 接口，并把结果解析为 JSON 对象。

        参数：
            context (dict[str, Any]): 单个 answer_item 的统一上下文。
            stage_type (str): 当前阶段类型。
            endpoint (ProviderEndpointConfig): 当前阶段使用的模型端点配置。
            messages (list[dict[str, Any]]): OpenAI 兼容消息列表。

        返回：
            ProviderResponse: 包含结构化输出、原始文本路径和模型名称的响应。
        """
        validate_endpoint_config(stage_type, endpoint)

        raw_text = self.post_chat_completion(endpoint=endpoint, messages=messages)
        parsed_output = normalize_provider_stage_output(
            stage_type=stage_type,
            context=context,
            parsed_output=parse_model_json(raw_text),
        )
        raw_output_path = save_raw_model_output(
            context=context,
            stage_type=stage_type,
            model_name=endpoint.model_name,
            raw_text=raw_text,
            parsed_output=parsed_output,
        )

        return ProviderResponse(
            output=parsed_output,
            raw_text=raw_text,
            raw_output_path=raw_output_path,
            model_name=endpoint.model_name,
        )

    def post_chat_completion(self, endpoint: ProviderEndpointConfig, messages: list[dict[str, Any]]) -> str:
        """
        功能：使用标准库向 OpenAI 兼容接口发送一次 chat/completions 请求。

        参数：
            endpoint (ProviderEndpointConfig): 模型端点配置。
            messages (list[dict[str, Any]]): OpenAI 兼容消息列表。

        返回：
            str: 模型返回的消息正文。
        """
        request_body = {
            "model": endpoint.model_name,
            "messages": messages,
            "temperature": endpoint.temperature,
            "max_tokens": endpoint.max_tokens,
            "stream": False,
        }
        if endpoint.reasoning_effort:
            request_body["reasoning_effort"] = endpoint.reasoning_effort
        if endpoint.thinking_type:
            request_body["extra_body"] = {"thinking": {"type": endpoint.thinking_type}}
        request_data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        request_url = endpoint.base_url.rstrip("/") + "/chat/completions"

        for attempt_index in range(endpoint.retry_count + 1):
            try:
                return post_chat_completion_once(
                    request_url=request_url,
                    request_body=request_body,
                    endpoint=endpoint,
                )
            except urllib.error.HTTPError as exc:
                error_body = read_http_error_body(exc)
                if endpoint.reasoning_effort and "reasoning_effort" in error_body:
                    fallback_body = dict(request_body)
                    fallback_body.pop("reasoning_effort", None)
                    return post_chat_completion_once(
                        request_url=request_url,
                        request_body=fallback_body,
                        endpoint=endpoint,
                    )
                if attempt_index >= endpoint.retry_count:
                    raise ValueError(f"模型调用失败：model={endpoint.model_name}，HTTP {exc.code}，原因={error_body}") from exc
                time.sleep(min(2**attempt_index, 5))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                if attempt_index >= endpoint.retry_count:
                    raise ValueError(f"模型调用失败：model={endpoint.model_name}，原因={exc}") from exc
                # 失败后短暂退避，避免网络抖动或服务端限流时立即重试造成更大压力。
                time.sleep(min(2**attempt_index, 5))

        raise ValueError("模型调用失败：未知错误")


def validate_endpoint_config(stage_type: str, endpoint: ProviderEndpointConfig) -> None:
    """
    功能：在真实调用前校验模型端点必要配置。

    参数：
        stage_type (str): 当前阶段类型。
        endpoint (ProviderEndpointConfig): 模型端点配置。

    返回：
        None
    """
    missing_fields = []
    if not endpoint.api_key:
        missing_fields.append("API key")
    if not endpoint.base_url:
        missing_fields.append("base URL")
    if not endpoint.model_name:
        missing_fields.append("model name")

    if missing_fields:
        # 不输出具体 key 值，避免把密钥写入日志或错误响应。
        raise ValueError(f"{stage_type} 模型配置不完整：缺少 {', '.join(missing_fields)}")


def build_image_source_url(answer_image: dict[str, Any]) -> str:
    """
    功能：根据答题图片来源构造视觉模型可消费的 image_url。

    参数：
        answer_image (dict[str, Any]): 包含 file_path 或 image_url 的图片输入对象。

    返回：
        str: http(s) 图片链接或本地图片转换后的 base64 data URL。
    """
    image_path = str(answer_image.get("file_path") or "").strip()
    image_url = str(answer_image.get("image_url") or "").strip()
    if image_path:
        return build_image_data_url(image_path)
    if image_url.startswith(("http://", "https://")):
        return image_url
    raise ValueError("student_answer.image_url 必须是 http:// 或 https:// 链接")


def build_image_data_url(image_path: str) -> str:
    """
    功能：把本地切题图转换为 OpenAI 兼容视觉输入使用的 data URL。

    参数：
        image_path (str): 本地图片路径。

    返回：
        str: 包含 MIME 类型和 base64 内容的 data URL。
    """
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"答题图不存在：{image_path}")

    suffix = path.suffix.lower()
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")

    encoded_image = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded_image}"


def extract_chat_message_content(response_json: dict[str, Any]) -> str:
    """
    功能：从 OpenAI 兼容响应中提取 assistant 消息正文。

    参数：
        response_json (dict[str, Any]): 接口返回的完整 JSON。

    返回：
        str: assistant 消息正文。
    """
    choices = response_json.get("choices") or []
    if not choices:
        raise ValueError("模型响应缺少 choices")

    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        content = "\n".join(text_parts)

    if not isinstance(content, str) or not content.strip():
        raise ValueError("模型响应正文为空")

    return content


def post_chat_completion_once(
    request_url: str,
    request_body: dict[str, Any],
    endpoint: ProviderEndpointConfig,
) -> str:
    """
    功能：发送一次 OpenAI 兼容 chat/completions 请求，不在本函数内做重试。

    参数：
        request_url (str): 完整请求地址。
        request_body (dict[str, Any]): 请求体。
        endpoint (ProviderEndpointConfig): 当前模型端点配置。

    返回：
        str: assistant 消息正文。
    """
    request_data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        request_url,
        data=request_data,
        headers={
            "Authorization": f"Bearer {endpoint.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=endpoint.timeout_seconds) as response:
        response_json = json.loads(response.read().decode("utf-8"))
        return extract_chat_message_content(response_json)


def read_http_error_body(error: urllib.error.HTTPError) -> str:
    """
    功能：读取 HTTPError 的响应正文，用于判断兼容接口是否拒绝某个参数。

    参数：
        error (urllib.error.HTTPError): urllib 抛出的 HTTP 错误。

    返回：
        str: 已解码的错误正文。
    """
    try:
        return error.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        # 错误正文读取失败时仍返回状态短语，避免上层错误信息为空。
        return str(error)


def save_raw_model_output(
    context: dict[str, Any],
    stage_type: str,
    model_name: str,
    raw_text: str,
    parsed_output: dict[str, Any],
) -> str:
    """
    功能：保存真实模型原始输出和解析后 JSON，供后续审计追溯。

    参数：
        context (dict[str, Any]): 单个 answer_item 的统一上下文。
        stage_type (str): 当前阶段类型。
        model_name (str): 实际调用的模型名称。
        raw_text (str): 模型返回的原始文本。
        parsed_output (dict[str, Any]): 解析后的结构化结果。

    返回：
        str: raw 输出文件路径。
    """
    if not should_save_raw_model_output():
        return ""

    if "answer_item" in context:
        project_id = context["answer_item"]["project_id"]
        answer_item_id = context["answer_item"]["id"]
        pipeline_run_id = context["pipeline_run"]["id"]
        output_dir = (
            get_config().project_data_dir
            / project_id
            / "pipeline_runs"
            / pipeline_run_id
            / answer_item_id
            / stage_type
        )
    else:
        project_id = context["project_id"]
        run_id = context.get("id", "rubric_gap_run")
        question_id = context.get("question", {}).get("id", "question")
        output_dir = (
            get_config().project_data_dir
            / project_id
            / "rubric_gap_runs"
            / str(run_id)
            / str(question_id)
            / stage_type
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "raw_response.json"
    output_payload = {
        "stage_type": stage_type,
        "model_name": model_name,
        "raw_text": raw_text,
        "parsed_output": parsed_output,
    }
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(output_path)


def should_save_raw_model_output() -> bool:
    """
    功能：读取是否保存模型原始输出的环境变量开关。

    参数：
        无

    返回：
        bool: 只有明确设置为 true、1、yes 或 on 时才保存 raw_response.json。
    """
    raw_value = get_env_value("AI_GRADING_SAVE_RAW_OUTPUT", "false").lower()
    return raw_value in {"true", "1", "yes", "on"}


def normalize_provider_stage_output(
    stage_type: str,
    context: dict[str, Any],
    parsed_output: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：把现有脚本 prompt 的真实模型输出适配为 backend stage 契约字段。

    参数：
        stage_type (str): 当前阶段类型。
        context (dict[str, Any]): 单个 answer_item 的统一上下文。
        parsed_output (dict[str, Any]): 模型返回的原始 JSON 对象。

    返回：
        dict[str, Any]: 可通过 stage 契约校验的输出对象。
    """
    if stage_type == "visual_ocr":
        return normalize_visual_output(context, parsed_output)
    if stage_type in {"grading", "scoring"}:
        return normalize_scoring_output(parsed_output)
    if stage_type == "evidence_risk":
        return normalize_evidence_risk_output(parsed_output)
    if stage_type == "method_analysis":
        return normalize_method_analysis_output(parsed_output)
    if stage_type == "homework_review":
        return normalize_homework_review_output(parsed_output)
    if stage_type in {"risk_gate", "review_decision", "homework_review_decision"}:
        return normalize_review_decision_output(parsed_output)
    if stage_type == "rubric_gap_discovery":
        return normalize_rubric_gap_discovery_output(parsed_output)
    return parsed_output


def normalize_visual_output(context: dict[str, Any], parsed_output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把视觉 prompt 输出适配为 visual_ocr stage 的最小契约。

    参数：
        context (dict[str, Any]): 单个 answer_item 的统一上下文。
        parsed_output (dict[str, Any]): 视觉模型返回的 JSON。

    返回：
        dict[str, Any]: visual_ocr stage 输出。
    """
    if {"transcription", "normalized_input", "ocr_risk"}.issubset(parsed_output.keys()):
        # 真实模型可能已经按完整契约返回 normalized_input，但模型本身不一定会回显图片来源。
        # 这里由代码侧强制补齐 image_path/image_url，方便 Django 侧审计和排查 URL 图片是否被正确传入。
        normalized_input = parsed_output.get("normalized_input")
        if isinstance(normalized_input, dict):
            normalized_input["image_path"] = context["answer_image"].get("file_path", "")
            normalized_input["image_url"] = context["answer_image"].get("image_url", "")
        return parsed_output

    blocks = parsed_output.get("blocks")
    if not isinstance(blocks, list):
        blocks = []

    student_answer_text = "\n".join(
        # Seed2.0 的真实视觉结果使用 text 字段；早期约定和 mock 结果可能使用 content 字段。
        # 同时读取两者，是为了让 normalized_input 保持稳定，避免下游批改和复核页依赖某一种供应商字段。
        str(block.get("text") or block.get("content") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("usable_for_grading") != "no"
    ).strip()
    visual_summary = parsed_output.get("visual_summary") if isinstance(parsed_output.get("visual_summary"), dict) else {}
    risk_observations = parsed_output.get("risk_observations") if isinstance(parsed_output.get("risk_observations"), list) else []

    return {
        "transcription": parsed_output,
        "normalized_input": {
            "answer_item_id": context["answer_item"]["id"],
            "question_no": context["answer_item"]["question_no"],
            "image_path": context["answer_image"].get("file_path", ""),
            "image_url": context["answer_image"].get("image_url", ""),
            "student_answer_text": student_answer_text,
            "final_answer_candidates": parsed_output.get("final_answer_candidates", []),
        },
        "ocr_risk": {
            "level": visual_summary.get("overall_risk") or visual_summary.get("risk_level") or "medium",
            "types": [
                observation.get("type", "visual_risk")
                for observation in risk_observations
                if isinstance(observation, dict)
            ],
            "observations": risk_observations,
        },
    }


def normalize_grading_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    return normalize_scoring_output(parsed_output)


def normalize_scoring_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把批改 prompt 输出适配为 grading stage 的最小契约。

    参数：
        parsed_output (dict[str, Any]): 批改模型返回的 JSON。

    返回：
        dict[str, Any]: scoring stage 输出。
    """
    if {"suggested_score", "max_score", "score_points", "deductions", "summary"}.issubset(parsed_output.keys()):
        normalized_output = dict(parsed_output)
        normalized_output.setdefault("selected_path_id", None)
        return normalized_output

    key_points = parsed_output.get("key_points") if isinstance(parsed_output.get("key_points"), list) else []
    score_points = []
    for point in key_points:
        if not isinstance(point, dict):
            continue
        score_points.append(
            {
                "id": point.get("point_id", ""),
                "description": point.get("description", ""),
                "status": point.get("status", "unclear"),
                "score": point.get("score_awarded", 0),
                "max_score": point.get("max_score", 0),
                "evidence": point.get("evidence", ""),
            }
        )

    return {
        "selected_path_id": parsed_output.get("selected_path_id"),
        "suggested_score": parsed_output.get("score", 0),
        "max_score": parsed_output.get("max_score", 1),
        "summary": parsed_output.get("grading_summary", ""),
        "score_points": score_points,
        "deductions": parsed_output.get("deductions", []),
        "mathematical_review": parsed_output.get("mathematical_review", {}),
        "evidence_usage": parsed_output.get("evidence_usage", {}),
        "final_answer_status": parsed_output.get("final_answer_status", "unclear"),
        "raw_scoring": parsed_output,
    }


def normalize_homework_review_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把作业评价模型的输出收敛为稳定契约，并兼容少量字段形态波动。

    参数：
        parsed_output (dict[str, Any]): 作业评价模型返回的 JSON。

    返回：
        dict[str, Any]: homework_review stage 可直接校验的输出。
    """
    allowed_statuses = {"correct", "partially_correct", "incorrect", "incomplete", "blank", "unclear"}
    answer_status = str(parsed_output.get("answer_status") or "unclear").lower()
    if answer_status not in allowed_statuses:
        answer_status = "unclear"

    allowed_issue_types = {"conceptual", "calculation", "logic", "notation", "missing_step", "final_answer"}
    normalized_issues = []
    raw_issues = parsed_output.get("issues")
    if not isinstance(raw_issues, list):
        raw_issues = []
    for raw_issue in raw_issues:
        if isinstance(raw_issue, dict):
            issue_type = str(raw_issue.get("type") or "conceptual").lower()
            if issue_type not in allowed_issue_types:
                issue_type = "conceptual"
            normalized_issues.append(
                {
                    "type": issue_type,
                    "location": str(raw_issue.get("location") or ""),
                    "evidence": str(raw_issue.get("evidence") or ""),
                    "explanation": str(raw_issue.get("explanation") or ""),
                    "correction_guidance": str(raw_issue.get("correction_guidance") or ""),
                }
            )
        elif isinstance(raw_issue, str) and raw_issue.strip():
            # 字符串问题项仍保留其信息，但统一落到概念问题，避免整个作业任务失败。
            normalized_issues.append(
                {
                    "type": "conceptual",
                    "location": "",
                    "evidence": "",
                    "explanation": raw_issue.strip(),
                    "correction_guidance": "",
                }
            )

    confidence_level = str(parsed_output.get("confidence_level") or "medium").lower()
    if confidence_level not in {"high", "medium", "low"}:
        confidence_level = "medium"
    review_advice = str(parsed_output.get("review_advice") or "sample_review").lower()
    if review_advice not in {"auto_accept", "sample_review", "required_review", "second_review"}:
        review_advice = "sample_review"

    return {
        "answer_status": answer_status,
        "summary": str(parsed_output.get("summary") or ""),
        "strengths": normalize_review_focus(parsed_output.get("strengths")),
        "issues": normalized_issues,
        "improvement_suggestions": normalize_review_focus(parsed_output.get("improvement_suggestions")),
        "confidence_level": confidence_level,
        "review_advice": review_advice,
        "risk_tags": normalize_review_focus(parsed_output.get("risk_tags")),
        "review_focus": normalize_review_focus(parsed_output.get("review_focus")),
        "evidence_safe": bool(parsed_output.get("evidence_safe", False)),
        "decision_reason": str(parsed_output.get("decision_reason") or ""),
        "evidence_usage": parsed_output.get("evidence_usage")
        if isinstance(parsed_output.get("evidence_usage"), dict)
        else {},
        "raw_homework_review": parsed_output,
    }


def normalize_risk_gate_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    return normalize_review_decision_output(parsed_output)


def normalize_review_decision_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把闸门 prompt 输出适配为 risk_gate stage 的最小契约。

    参数：
        parsed_output (dict[str, Any]): 闸门模型返回的 JSON。

    返回：
        dict[str, Any]: review_decision stage 输出。
    """
    if {"confidence_level", "review_advice", "risk_tags", "review_focus", "evidence_safe"}.issubset(parsed_output.keys()):
        normalized_output = dict(parsed_output)
        # 真实模型偶尔会把单条复核建议输出成字符串或对象；这里统一收敛为数组，避免整题失败。
        normalized_output["review_focus"] = normalize_review_focus(parsed_output.get("review_focus"))
        if not isinstance(normalized_output.get("risk_tags"), list):
            normalized_output["risk_tags"] = normalize_review_focus(normalized_output.get("risk_tags"))
        if "policy_overrides" in normalized_output and not isinstance(normalized_output.get("policy_overrides"), list):
            normalized_output["policy_overrides"] = normalize_review_focus(normalized_output.get("policy_overrides"))
        return normalized_output

    risk_level = str(parsed_output.get("risk_level") or "medium").lower()
    if risk_level not in {"high", "medium", "low"}:
        risk_score = int(parsed_output.get("risk_score") or 50)
        risk_level = "high" if risk_score >= 60 else "medium" if risk_score >= 30 else "low"

    review_required = bool(parsed_output.get("review_required", False))
    auto_grade_allowed = bool(parsed_output.get("auto_grade_allowed", not review_required))
    if review_required:
        review_advice = "required_review" if risk_level == "high" else "sample_review"
    else:
        review_advice = "auto_accept"

    risk_tags = parsed_output.get("risk_types") if isinstance(parsed_output.get("risk_types"), list) else []
    if not risk_tags and parsed_output.get("unsafe_grading_judgements"):
        risk_tags = ["ocr_evidence_risk"]

    return {
        "confidence_level": "high" if auto_grade_allowed and risk_level == "low" else risk_level,
        "review_advice": review_advice,
        "risk_tags": risk_tags,
        "review_focus": normalize_review_focus(parsed_output.get("review_focus", [])),
        "evidence_safe": auto_grade_allowed and not review_required,
        "decision_reason": parsed_output.get("review_reason", ""),
        "policy_overrides": parsed_output.get("policy_overrides", []),
        "raw_review_decision": parsed_output,
    }


def normalize_review_focus(value: Any) -> list[str]:
    """
    功能：把模型返回的复核重点字段统一转换为字符串数组。

    参数：
        value (Any): 模型返回的 review_focus、risk_tags 或类似数组字段。

    返回：
        list[str]: 可通过 stage 契约校验的字符串数组。
    """
    if value is None:
        return []
    if isinstance(value, list):
        normalized_items = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                item_text = item.strip()
            else:
                # 对象或数字等异常形态保留原始信息，便于教师和日志侧追溯模型意图。
                item_text = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
            if item_text:
                normalized_items.append(item_text)
        return normalized_items
    if isinstance(value, str):
        value_text = value.strip()
        return [value_text] if value_text else []
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False)]
    return [str(value)]


def normalize_evidence_risk_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    if {
        "evidence_risk_level",
        "risk_tags",
        "risk_locations",
        "uncertain_symbols",
        "consistency_risks",
        "final_answer_risks",
        "grading_constraints",
    }.issubset(parsed_output.keys()):
        return parsed_output

    risk_tags = parsed_output.get("risk_types") if isinstance(parsed_output.get("risk_types"), list) else []
    risk_level = str(parsed_output.get("risk_level") or "medium").lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"

    return {
        "evidence_risk_level": risk_level,
        "risk_tags": risk_tags,
        "risk_locations": parsed_output.get("review_focus", []),
        "uncertain_symbols": parsed_output.get("uncertain_symbols", []),
        "consistency_risks": parsed_output.get("consistency_risks", []),
        "final_answer_risks": parsed_output.get("final_answer_risks", []),
        "grading_constraints": parsed_output.get("safe_grading_judgements", []),
        "raw_evidence_risk": parsed_output,
    }


def normalize_method_analysis_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    if {"method_summary", "method_confidence", "method_evidence_blocks"}.issubset(parsed_output.keys()):
        return parsed_output

    mathematical_review = parsed_output.get("mathematical_review")
    method_summary = ""
    if isinstance(mathematical_review, dict):
        method_summary = str(mathematical_review.get("method_detected") or "")

    return {
        "method_summary": str(parsed_output.get("method_summary") or method_summary),
        "method_confidence": str(parsed_output.get("method_confidence") or "medium").lower()
        if str(parsed_output.get("method_confidence") or "medium").lower() in {"low", "medium", "high"}
        else "medium",
        "method_evidence_blocks": parsed_output.get("method_evidence_blocks", []),
        "raw_method_analysis": parsed_output,
    }


def normalize_rubric_gap_discovery_output(parsed_output: dict[str, Any]) -> dict[str, Any]:
    method_clusters = parsed_output.get("method_clusters")
    if not isinstance(method_clusters, list):
        method_clusters = []

    gap_candidates = parsed_output.get("gap_candidates")
    if not isinstance(gap_candidates, list):
        gap_candidates = []

    normalized_clusters = []
    for cluster in method_clusters:
        if not isinstance(cluster, dict):
            continue
        normalized_clusters.append(
            {
                "cluster_name": str(cluster.get("cluster_name") or cluster.get("method") or "未命名解法"),
                "count": int(cluster.get("count") or 0),
                "representative_answer_item_ids": cluster.get("representative_answer_item_ids")
                if isinstance(cluster.get("representative_answer_item_ids"), list)
                else [],
                "representative_summaries": cluster.get("representative_summaries")
                if isinstance(cluster.get("representative_summaries"), list)
                else [],
                "reason": str(cluster.get("reason") or ""),
            }
        )

    normalized_candidates = []
    for candidate in gap_candidates:
        if not isinstance(candidate, dict):
            continue
        coverage_status = str(candidate.get("coverage_status") or "unclear")
        if coverage_status not in {"covered", "not_covered", "unclear"}:
            coverage_status = "unclear"
        candidate_type = str(candidate.get("candidate_type") or "legacy")
        scoring_path_draft = candidate.get("scoring_path_draft") if isinstance(candidate.get("scoring_path_draft"), dict) else {}
        normalized_candidates.append(
            {
                "candidate_type": candidate_type,
                "method": str(candidate.get("method") or candidate.get("cluster_name") or "未命名解法"),
                "count": int(candidate.get("count") or 0),
                "coverage_status": coverage_status,
                "representative_answer_item_ids": candidate.get("representative_answer_item_ids")
                if isinstance(candidate.get("representative_answer_item_ids"), list)
                else [],
                "representative_summaries": candidate.get("representative_summaries")
                if isinstance(candidate.get("representative_summaries"), list)
                else [],
                "reason": str(candidate.get("reason") or ""),
                "scoring_path_draft": scoring_path_draft,
                "exclusion_reason": str(candidate.get("exclusion_reason") or ""),
            }
        )

    excluded_candidates = parsed_output.get("excluded_candidates")
    if not isinstance(excluded_candidates, list):
        excluded_candidates = []

    return {
        "method_clusters": normalized_clusters,
        "gap_candidates": normalized_candidates,
        "excluded_candidates": excluded_candidates,
        "raw_rubric_gap_discovery": parsed_output,
    }


# 作业评价的说明字段与方法聚合共用底层公式识别规则，但分别维护字段白名单以避免误扫审计数据。
HOMEWORK_LATEX_TEXT_FIELDS = frozenset(
    {
        "summary",
        "location",
        "evidence",
        "explanation",
        "correction_guidance",
        "decision_reason",
    }
)
HOMEWORK_LATEX_LIST_FIELDS = frozenset(
    {
        "strengths",
        "improvement_suggestions",
        "review_focus",
        "uncertain_content",
    }
)


def find_homework_review_latex_issues(output: dict[str, Any]) -> list[str]:
    """
    功能：检查作业评价展示字段中是否存在未使用 LaTeX 包裹的明显数学表达式。

    参数：
        output (dict[str, Any]): 已规范化的作业评价输出。

    返回：
        list[str]: 字段路径和问题类型列表；空列表表示未发现明显问题。
    """
    issues: list[str] = []
    for field_path, text_value in iter_homework_review_display_texts(output):
        text_issues = find_text_latex_issues(text_value)
        if text_issues:
            issues.append(f"{field_path}: {', '.join(text_issues)}")
        if len(issues) >= 50:
            break
    return issues


def iter_homework_review_display_texts(value: Any, path: str = "") -> list[tuple[str, str]]:
    """
    功能：递归提取作业评价中可能包含数学表达式的展示文本。

    参数：
        value (Any): 当前遍历的字典、数组或标量。
        path (str): 当前值在 JSON 中的字段路径。

    返回：
        list[tuple[str, str]]: 字段路径与文本内容列表。
    """
    display_texts: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child_value in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "raw_homework_review":
                continue
            if key in HOMEWORK_LATEX_TEXT_FIELDS and isinstance(child_value, str):
                display_texts.append((child_path, child_value))
            elif key in HOMEWORK_LATEX_LIST_FIELDS and isinstance(child_value, list):
                for item_index, item_value in enumerate(child_value):
                    if isinstance(item_value, str):
                        display_texts.append((f"{child_path}[{item_index}]", item_value))
            if isinstance(child_value, (dict, list)):
                display_texts.extend(iter_homework_review_display_texts(child_value, child_path))
    elif isinstance(value, list):
        for item_index, item_value in enumerate(value):
            if isinstance(item_value, (dict, list)):
                display_texts.extend(iter_homework_review_display_texts(item_value, f"{path}[{item_index}]"))
    return display_texts


def build_homework_review_structure_signature(output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：提取公式纠正前后必须保持不变的作业评价结构。

    参数：
        output (dict[str, Any]): 已规范化的作业评价输出。

    返回：
        dict[str, Any]: 作答状态、问题类型和可信度组成的结构签名。
    """
    issue_types = []
    for issue in output.get("issues", []):
        if isinstance(issue, dict):
            issue_types.append(issue.get("type"))
    return {
        "answer_status": output.get("answer_status"),
        "issue_types": issue_types,
        "confidence_level": output.get("confidence_level"),
        "review_advice": output.get("review_advice"),
        "evidence_safe": output.get("evidence_safe"),
    }


# 这里只检查模型生成、且会直接展示给教师的文本字段；ID、状态和原始审计对象不参与公式检查。
RUBRIC_GAP_LATEX_TEXT_FIELDS = frozenset(
    {
        "cluster_name",
        "reason",
        "method",
        "path_name",
        "applicability",
        "description",
        "required_evidence",
        "partial_credit_rule",
        "notes_for_ai",
        "why_not_covered_by_current_rubric",
        "exclusion_reason",
    }
)
RUBRIC_GAP_LATEX_LIST_FIELDS = frozenset({"representative_summaries"})

# 这些模式只覆盖确定性较高的公式特征，宁可交给提示词约束，也不把普通英文误判为公式。
RAW_LATEX_COMMAND_PATTERN = re.compile(
    r"\\(?:frac|dfrac|tfrac|sum|prod|int|sqrt|lim|lambda|alpha|beta|gamma|theta|mu|pi|infty|"
    r"begin|end|left|right|geq|leq|neq|cdot|times)\b"
)
UNICODE_MATH_SYMBOL_PATTERN = re.compile(r"[∑∏∫√∞≠≤≥≈±×÷∂∇∈∉⊂⊆⊃⊇∪∩→⇒⇔]")
VARIABLE_EXPRESSION_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-zΑ-Ωα-ω](?:_[A-Za-z0-9]+|\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?:=|<=|>=|<|>)\s*(?:[A-Za-zΑ-Ωα-ω0-9({\\])"
)
POWER_OR_SUBSCRIPT_PATTERN = re.compile(r"(?<![\w])[A-Za-zΑ-Ωα-ω][A-Za-z0-9]*[_^](?:\{[^}]+\}|[A-Za-z0-9])")
NUMERIC_FRACTION_PATTERN = re.compile(r"(?<![\w])\d+\s*/\s*\d+(?![\w])")
FUNCTION_EXPRESSION_PATTERN = re.compile(r"(?<![\w])[A-Za-zΑ-Ωα-ω]\s*\([^)]*(?:\d+[A-Za-z]|[+*/^=])")


def find_rubric_gap_latex_issues(output: dict[str, Any]) -> list[str]:
    """
    功能：检查方法聚合展示字段中是否存在未使用 LaTeX 包裹的明显数学表达式。

    参数：
        output (dict[str, Any]): 已规范化的方法聚合模型输出。

    返回：
        list[str]: 包含字段路径和问题类型的列表；空列表表示未发现明显问题。
    """
    issues: list[str] = []
    for field_path, text_value in iter_rubric_gap_display_texts(output):
        text_issues = find_text_latex_issues(text_value)
        if text_issues:
            issues.append(f"{field_path}: {', '.join(text_issues)}")

        # 限制问题数量，避免异常模型输出生成过大的纠正请求和错误信息。
        if len(issues) >= 50:
            break

    return issues


def build_rubric_gap_structure_signature(output: dict[str, Any]) -> dict[str, Any]:
    """
    功能：提取公式纠正前后必须保持不变的方法聚合结构字段。

    参数：
        output (dict[str, Any]): 已规范化的方法聚合输出。

    返回：
        dict[str, Any]: 不包含说明性文本、但保留计数、ID、分值和状态的结构签名。
    """
    cluster_signatures = []
    for cluster in output.get("method_clusters", []):
        if not isinstance(cluster, dict):
            continue
        cluster_signatures.append(
            {
                "count": cluster.get("count"),
                "representative_answer_item_ids": cluster.get("representative_answer_item_ids", []),
            }
        )

    candidate_signatures = []
    for candidate in output.get("gap_candidates", []):
        if not isinstance(candidate, dict):
            continue
        scoring_path = candidate.get("scoring_path_draft")
        if not isinstance(scoring_path, dict):
            scoring_path = {}
        point_signatures = []
        for point in scoring_path.get("points", []):
            if isinstance(point, dict):
                point_signatures.append({"id": point.get("id"), "score": point.get("score")})
        candidate_signatures.append(
            {
                "candidate_type": candidate.get("candidate_type"),
                "count": candidate.get("count"),
                "coverage_status": candidate.get("coverage_status"),
                "representative_answer_item_ids": candidate.get("representative_answer_item_ids", []),
                "path_id": scoring_path.get("id"),
                "points": point_signatures,
            }
        )

    excluded_signatures = []
    for candidate in output.get("excluded_candidates", []):
        if not isinstance(candidate, dict):
            continue
        excluded_signatures.append(
            {
                "candidate_type": candidate.get("candidate_type"),
                "count": candidate.get("count"),
                "representative_answer_item_ids": candidate.get("representative_answer_item_ids", []),
            }
        )

    return {
        "method_clusters": cluster_signatures,
        "gap_candidates": candidate_signatures,
        "excluded_candidates": excluded_signatures,
    }


def iter_rubric_gap_display_texts(value: Any, path: str = "") -> list[tuple[str, str]]:
    """
    功能：递归提取方法聚合结果中需要满足 LaTeX 要求的展示文本。

    参数：
        value (Any): 当前遍历的字典、数组或标量值。
        path (str): 当前值在输出 JSON 中的可读字段路径。

    返回：
        list[tuple[str, str]]: 字段路径与文本内容组成的列表。
    """
    display_texts: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child_value in value.items():
            child_path = f"{path}.{key}" if path else key

            # 原始审计对象会重复包含同一份模型输出，跳过它可以避免重复报错。
            if key == "raw_rubric_gap_discovery":
                continue

            if key in RUBRIC_GAP_LATEX_TEXT_FIELDS and isinstance(child_value, str):
                display_texts.append((child_path, child_value))
            elif key in RUBRIC_GAP_LATEX_LIST_FIELDS and isinstance(child_value, list):
                for item_index, item_value in enumerate(child_value):
                    if isinstance(item_value, str):
                        display_texts.append((f"{child_path}[{item_index}]", item_value))

            # 即使当前字段已经被采集，也继续进入字典或数组，以覆盖评分路径中的深层字段。
            if isinstance(child_value, (dict, list)):
                display_texts.extend(iter_rubric_gap_display_texts(child_value, child_path))

    elif isinstance(value, list):
        for item_index, item_value in enumerate(value):
            item_path = f"{path}[{item_index}]"
            if isinstance(item_value, (dict, list)):
                display_texts.extend(iter_rubric_gap_display_texts(item_value, item_path))

    return display_texts


def find_text_latex_issues(text: str) -> list[str]:
    """
    功能：检查单个展示文本中的 LaTeX 定界符和明显裸公式特征。

    参数：
        text (str): 待检查的展示文本。

    返回：
        list[str]: 当前文本命中的问题类型。
    """
    outside_text, delimiter_balanced = extract_text_outside_latex(text)
    issues: list[str] = []
    if not delimiter_balanced:
        issues.append("LaTeX 定界符未闭合")

    # 只检查公式定界符外的文本，已经正确放在 $...$ 或 $$...$$ 中的内容不会误报。
    if RAW_LATEX_COMMAND_PATTERN.search(outside_text):
        issues.append("LaTeX 命令未放入公式定界符")
    if UNICODE_MATH_SYMBOL_PATTERN.search(outside_text):
        issues.append("数学符号未放入公式定界符")
    if VARIABLE_EXPRESSION_PATTERN.search(outside_text):
        issues.append("变量表达式未放入公式定界符")
    if POWER_OR_SUBSCRIPT_PATTERN.search(outside_text):
        issues.append("上下标表达式未放入公式定界符")
    if NUMERIC_FRACTION_PATTERN.search(outside_text):
        issues.append("分式未放入公式定界符")
    if FUNCTION_EXPRESSION_PATTERN.search(outside_text):
        issues.append("函数表达式未放入公式定界符")

    return issues


def extract_text_outside_latex(text: str) -> tuple[str, bool]:
    """
    功能：移除已由 LaTeX 定界符包裹的内容，保留需要继续检查的普通文本。

    参数：
        text (str): 可能同时包含自然语言和 LaTeX 公式的文本。

    返回：
        tuple[str, bool]: 公式外文本，以及所有公式定界符是否正确闭合。
    """
    outside_characters: list[str] = []
    active_delimiter = ""
    index = 0

    while index < len(text):
        # 转义美元符号不是公式边界，应作为普通文本保留。
        if text[index] == "\\" and index + 1 < len(text) and text[index + 1] == "$":
            if not active_delimiter:
                outside_characters.append("$")
            index += 2
            continue

        if not active_delimiter:
            if text.startswith("$$", index):
                active_delimiter = "$$"
                index += 2
                continue
            if text[index] == "$":
                active_delimiter = "$"
                index += 1
                continue

            outside_characters.append(text[index])
            index += 1
            continue

        if text.startswith(active_delimiter, index):
            index += len(active_delimiter)
            active_delimiter = ""
            # 插入空格，防止公式两侧的普通文本在移除公式后意外拼接成新表达式。
            outside_characters.append(" ")
            continue

        index += 1

    return "".join(outside_characters), not active_delimiter
