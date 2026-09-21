from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    sys.path.insert(0, str(HANDOFF_ROOT))

# 专项测试直接访问运行时 provider，因此需要显式加入与公共包入口一致的运行时搜索路径。
RUNTIME_ROOT = HANDOFF_ROOT / "ai_grading_service" / "_runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

os.environ["AI_GRADING_SAVE_RAW_OUTPUT"] = "false"

from app.providers.config import ProviderEndpointConfig  # noqa: E402
from app.providers.openai_compatible import (  # noqa: E402
    OpenAICompatibleProvider,
    find_rubric_gap_latex_issues,
)


class MethodClusterLatexTest(unittest.TestCase):
    """验证方法聚合报告的 LaTeX 检查和一次纠正流程。"""

    def setUp(self) -> None:
        """
        功能：为真实 provider 的方法聚合调用构造不访问网络的最小上下文。

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
            "project_id": "latex_test_project",
            "id": "latex_test_run",
            "question": {"id": "q1", "max_score": 10},
            "rubric": {"scoring_points": []},
            "summary_pool": [],
            "threshold": {"min_cluster_count": 1},
        }

    def build_provider(self, model_outputs: list[dict[str, object]]) -> OpenAICompatibleProvider:
        """
        功能：构造使用预设模型返回值的 provider，便于验证纠正重试次数和结果。

        参数：
            model_outputs (list[dict[str, object]]): 每次模型调用依次返回的 JSON 对象。

        返回：
            OpenAICompatibleProvider: 不会发起真实 HTTP 请求的 provider。
        """
        provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
        provider.config = SimpleNamespace(grading=self.endpoint)
        provider.model_name = self.endpoint.model_name
        provider.prompts = {"rubric_gap_discovery": "测试用方法聚合提示词"}
        provider.post_chat_completion = Mock(
            side_effect=[json.dumps(output, ensure_ascii=False) for output in model_outputs]
        )
        return provider

    def test_validator_accepts_renderable_latex_in_nested_fields(self) -> None:
        """验证评分路径深层字段中的合法 LaTeX 不会被误报。"""
        output = {
            "method_clusters": [
                {
                    "cluster_name": "部分和下界法",
                    "representative_summaries": ["证明 $S_{2k} \\ge 1 + \\frac{k}{2}$。"],
                    "reason": "都通过部分和 $S_{2k}$ 建立下界。",
                }
            ],
            "gap_candidates": [
                {
                    "method": "柯西准则",
                    "reason": "取 $p=N$ 后证明尾和不趋于零。",
                    "scoring_path_draft": {
                        "applicability": "作答使用 $\\sum_{k=N+1}^{2N} \\frac{1}{k}$。",
                        "points": [
                            {
                                "description": "推出尾和 $\\ge \\frac{1}{2}$。",
                                "required_evidence": "看到 $p=N$ 和正确下界。",
                            }
                        ],
                    },
                }
            ],
            "excluded_candidates": [],
        }

        self.assertEqual(find_rubric_gap_latex_issues(output), [])

    def test_validator_does_not_treat_method_names_as_formulas(self) -> None:
        """验证带连字符的英文方法名称不会被当成未包裹的算式。"""
        output = {
            "method_clusters": [
                {
                    "cluster_name": "Euler-Lagrange 方法",
                    "representative_summaries": ["使用 Euler-Lagrange 方法建立必要条件。"],
                    "reason": "两份作答采用相同的 Euler-Lagrange 方法。",
                }
            ],
            "gap_candidates": [],
            "excluded_candidates": [],
        }

        self.assertEqual(find_rubric_gap_latex_issues(output), [])

    def test_validator_reports_unwrapped_formula_and_unclosed_delimiter(self) -> None:
        """验证裸变量表达式、Unicode 数学符号和未闭合公式都会被定位。"""
        output = {
            "method_clusters": [
                {
                    "cluster_name": "由 S_n=S_{n-1}+1/n 递推",
                    "representative_summaries": ["推出 ∑ 尾和大于 1/2"],
                    "reason": "使用 $S_{2k} 建立下界",
                }
            ],
            "gap_candidates": [],
            "excluded_candidates": [],
        }

        issues = find_rubric_gap_latex_issues(output)

        self.assertTrue(any("cluster_name" in issue and "变量表达式" in issue for issue in issues))
        self.assertTrue(any("representative_summaries[0]" in issue for issue in issues))
        self.assertTrue(any("reason" in issue and "定界符未闭合" in issue for issue in issues))

    def test_provider_repairs_invalid_formula_once(self) -> None:
        """验证首次聚合结果包含裸公式时会调用模型一次进行格式纠正。"""
        invalid_output = self.build_cluster_output("由 S_n=S_{n-1}+1/n 得到结论")
        valid_output = self.build_cluster_output("由 $S_n=S_{n-1}+\\frac{1}{n}$ 得到结论")
        provider = self.build_provider([invalid_output, valid_output])

        response = provider.run_rubric_gap_discovery(self.context)

        self.assertEqual(provider.post_chat_completion.call_count, 2)
        self.assertEqual(find_rubric_gap_latex_issues(response.output), [])
        self.assertIn("$S_n", response.output["method_clusters"][0]["reason"])

    def test_provider_rejects_second_invalid_formula(self) -> None:
        """验证纠正后仍包含裸公式时返回明确错误，不继续交付聚合报告。"""
        invalid_output = self.build_cluster_output("由 S_n=S_{n-1}+1/n 得到结论")
        provider = self.build_provider([invalid_output, invalid_output])

        with self.assertRaisesRegex(ValueError, "仍不满足 LaTeX 要求"):
            provider.run_rubric_gap_discovery(self.context)

        self.assertEqual(provider.post_chat_completion.call_count, 2)

    def test_provider_rejects_structure_changes_during_formula_repair(self) -> None:
        """验证公式纠正调用不能同时篡改聚类计数等业务结构。"""
        invalid_output = self.build_cluster_output("由 S_n=S_{n-1}+1/n 得到结论")
        changed_output = self.build_cluster_output("由 $S_n=S_{n-1}+\\frac{1}{n}$ 得到结论")
        changed_output["method_clusters"][0]["count"] = 3
        provider = self.build_provider([invalid_output, changed_output])

        with self.assertRaisesRegex(ValueError, "改变了聚类或评分路径结构"):
            provider.run_rubric_gap_discovery(self.context)

        self.assertEqual(provider.post_chat_completion.call_count, 2)

    @staticmethod
    def build_cluster_output(reason: str) -> dict[str, object]:
        """
        功能：构造只改变聚类原因公式格式的最小模型输出。

        参数：
            reason (str): 放入 method_clusters[].reason 的测试文本。

        返回：
            dict[str, object]: 可被聚合结果规范化函数处理的模型输出。
        """
        return {
            "method_clusters": [
                {
                    "cluster_name": "递推下界法",
                    "count": 2,
                    "representative_answer_item_ids": ["a1", "a2"],
                    "representative_summaries": [],
                    "reason": reason,
                }
            ],
            "gap_candidates": [],
            "excluded_candidates": [],
        }


if __name__ == "__main__":
    unittest.main()
