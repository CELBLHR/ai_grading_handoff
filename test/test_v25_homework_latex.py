from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = HANDOFF_ROOT / "ai_grading_service" / "_runtime"
for import_path in [HANDOFF_ROOT, RUNTIME_ROOT]:
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

os.environ["AI_GRADING_SAVE_RAW_OUTPUT"] = "false"

from app.providers.config import ProviderEndpointConfig  # noqa: E402
from app.providers.openai_compatible import (  # noqa: E402
    OpenAICompatibleProvider,
    find_homework_review_latex_issues,
)


class HomeworkLatexTest(unittest.TestCase):
    """验证作业评价公式检查、纠正重试和结构保护。"""

    def setUp(self) -> None:
        """
        功能：构造不会访问真实网络的作业评价 provider 上下文。

        参数：
            无

        返回：
            None
        """
        self.endpoint = ProviderEndpointConfig(
            api_key="test-key",
            base_url="https://example.invalid",
            model_name="test-model",
            timeout_seconds=1,
            retry_count=0,
            temperature=0.0,
            max_tokens=4096,
            reasoning_effort="",
            thinking_type="disabled",
        )
        self.context = {
            "answer_item": {"id": "a1", "question_no": "1", "project_id": "p1", "max_score": None},
            "question": {"id": "q1", "question_type": "calculation", "standard_answer": ""},
            "student": {"student_no": "s1", "student_name": "测试学生"},
            "pipeline_run": {"id": "r1"},
        }

    def build_provider(self, model_outputs: list[dict[str, object]]) -> OpenAICompatibleProvider:
        """
        功能：构造按顺序返回预设 JSON 的真实 provider 替身。

        参数：
            model_outputs (list[dict[str, object]]): 各次模型调用应返回的 JSON 对象。

        返回：
            OpenAICompatibleProvider: HTTP 调用已替换为 Mock 的 provider。
        """
        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        provider.config = SimpleNamespace(grading=self.endpoint)
        provider.model_name = self.endpoint.model_name
        provider.prompts = {}
        provider.post_chat_completion = Mock(
            side_effect=[json.dumps(output, ensure_ascii=False) for output in model_outputs]
        )
        return provider

    def test_validator_checks_nested_homework_fields(self) -> None:
        """验证问题说明和订正建议中的裸公式会被定位。"""
        output = self.build_output("由 S_n=S_{n-1}+1/n 得到结论")

        issues = find_homework_review_latex_issues(output)

        self.assertTrue(any("issues[0].explanation" in issue for issue in issues))

    def test_provider_repairs_homework_formula_once(self) -> None:
        """验证首次作业评价含裸公式时只追加一次格式纠正调用。"""
        invalid_output = self.build_output("由 S_n=S_{n-1}+1/n 得到结论")
        valid_output = self.build_output("由 $S_n=S_{n-1}+\\frac{1}{n}$ 得到结论")
        provider = self.build_provider([invalid_output, valid_output])

        response = provider.run_homework_review(self.context, {}, {}, {})

        self.assertEqual(provider.post_chat_completion.call_count, 2)
        self.assertEqual(find_homework_review_latex_issues(response.output), [])

    def test_provider_rejects_second_invalid_homework_formula(self) -> None:
        """验证第二次结果仍含裸公式时不会交付不可渲染评价。"""
        invalid_output = self.build_output("由 S_n=S_{n-1}+1/n 得到结论")
        provider = self.build_provider([invalid_output, invalid_output])

        with self.assertRaisesRegex(ValueError, "仍不满足 LaTeX 要求"):
            provider.run_homework_review(self.context, {}, {}, {})

    def test_provider_rejects_structure_change_during_formula_repair(self) -> None:
        """验证格式纠正不能同时改变作答状态或问题类型。"""
        invalid_output = self.build_output("由 S_n=S_{n-1}+1/n 得到结论")
        changed_output = self.build_output("由 $S_n=S_{n-1}+\\frac{1}{n}$ 得到结论")
        changed_output["answer_status"] = "incorrect"
        provider = self.build_provider([invalid_output, changed_output])

        with self.assertRaisesRegex(ValueError, "改变了评价结构"):
            provider.run_homework_review(self.context, {}, {}, {})

    @staticmethod
    def build_output(explanation: str) -> dict[str, object]:
        """
        功能：构造仅改变问题说明公式格式的完整作业评价模型输出。

        参数：
            explanation (str): 放入 issues[].explanation 的测试文本。

        返回：
            dict[str, object]: 可通过作业评价规范化的模型输出。
        """
        return {
            "answer_status": "partially_correct",
            "summary": "学生已建立递推关系，但需要订正表达。",
            "strengths": ["能够识别递推思路。"],
            "issues": [
                {
                    "type": "notation",
                    "location": "第一步",
                    "evidence": "递推式",
                    "explanation": explanation,
                    "correction_guidance": "使用规范公式重新书写。",
                }
            ],
            "improvement_suggestions": ["核对递推式的下标。"],
            "confidence_level": "high",
            "review_advice": "auto_accept",
            "risk_tags": [],
            "review_focus": [],
            "evidence_safe": True,
            "decision_reason": "作答证据清楚。",
            "evidence_usage": {"used_evidence_risks": [], "uncertain_content": []},
        }


if __name__ == "__main__":
    unittest.main()
