from __future__ import annotations

import json
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    sys.path.insert(0, str(HANDOFF_ROOT))

os.environ["AI_PIPELINE_PROVIDER"] = "mock"

from ai_grading_service import (  # noqa: E402
    __version__,
    generate_question_from_image,
    generate_question_from_text,
    grade_answer,
)
from app.prompts.loader import load_prompt  # noqa: E402
from ai_grading_service.question_generation_api import (  # noqa: E402
    apply_formula_free_label_fallbacks,
    find_question_generation_quality_issues,
)


class RepairingQuestionProvider:
    """提供首次返回裸公式、第二次只修复格式的可控测试 provider。"""

    model_name = "repairing-test-model"

    def run_question_config_generator(self, context: dict) -> dict:
        """
        功能：构造包含裸公式和公式标题的首次生成结果。

        参数：
            context (dict): 题目配置生成上下文。

        返回：
            dict: 用于触发最终质量门的模型结果。
        """
        return {
            "question_title": "求 f(x) 的值",
            "question_type": "calculation",
            "question_type_confidence": "high",
            "standard_answers": ["2"],
            "solution_text": "计算 1+1=2。",
            "scoring_paths": [
                {
                    "id": "path_1",
                    "name": "f(x)直接计算",
                    "reference_solution": "计算 1+1=2。",
                    "applicability": "适用于 1+1 类型的题目。",
                    "points": [
                        {"id": "point_1", "score": 10, "description": "得到 1+1=2。"},
                    ],
                }
            ],
            "warnings": [],
        }

    def run_question_output_formatter(self, context: dict) -> dict:
        """
        功能：保持业务结构不变，只修复公式包裹及名称字段。

        参数：
            context (dict): 包含原始最终响应和校验问题的修复上下文。

        返回：
            dict: 符合 v2.6.1 质量要求的完整最终响应。
        """
        repaired_output = deepcopy(context["original_output"])
        repaired_output["question_title"] = "基础加法计算"
        repaired_output["question_content"] = "计算 $1+1$。"
        repaired_output["standard_answers"] = ["$2$"]
        repaired_output["solution_text"] = "计算 $1+1=2$。"
        repaired_path = repaired_output["scoring_paths"][0]
        repaired_path["name"] = "直接计算法"
        repaired_path["reference_solution"] = "计算 $1+1=2$。"
        repaired_path["applicability"] = "适用于 $1+1$ 类型的题目。"
        repaired_path["points"][0]["description"] = "得到 $1+1=2$。"
        return repaired_output


class StructureChangingQuestionProvider(RepairingQuestionProvider):
    """提供会在格式修复时篡改分值的异常测试 provider。"""

    def run_question_output_formatter(self, context: dict) -> dict:
        """
        功能：模拟修复模型错误改变评分点分值的情形。

        参数：
            context (dict): 包含原始最终响应的修复上下文。

        返回：
            dict: 分值已被错误修改的完整响应。
        """
        repaired_output = super().run_question_output_formatter(context)
        repaired_output["scoring_paths"][0]["points"][0]["score"] = 9
        return repaired_output


class QuestionGenerationTest(unittest.TestCase):
    """验证 v2.6.1 两个题目配置生成入口、格式质量门及旧批改兼容性。"""

    def test_text_exam_generates_complete_configuration_with_one_call(self) -> None:
        """验证考题题干方法一次调用即可生成标题、题型、答案和评分路径。"""
        result = generate_question_from_text(
            {
                "grading_mode": "exam",
                "question_content": "计算 $1+1$。",
                "score": 10,
            }
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["question_type"], "calculation")
        self.assertEqual(result["question_type_source"], "ai_inferred")
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["score_source"], "input")
        self.assertTrue(result["is_answer_ai_generated"])
        self.assertTrue(result["is_scoring_paths_ai_generated"])
        self.assertTrue(result["solution_text"])
        self.assertEqual(result["model_call_count"], 1)
        self.assertEqual(sum(point["score"] for point in result["scoring_paths"][0]["points"]), 10)

    def test_text_homework_does_not_accept_or_generate_score(self) -> None:
        """验证作业题干方法不使用分数，也不生成评分路径。"""
        result = generate_question_from_text(
            {
                "grading_mode": "homework",
                "question_content": "计算 $1+1$。",
            }
        )

        self.assertEqual(result["status"], "success")
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_source"], "not_applicable")
        self.assertEqual(result["scoring_paths"], [])
        self.assertEqual(result["solution_text"], "")
        self.assertIsNone(result["is_scoring_paths_ai_generated"])
        self.assertEqual(result["model_call_count"], 1)

    def test_image_stem_only_uses_default_score_and_two_calls(self) -> None:
        """验证只有题干的图片使用默认 10 分，并调用识别与生成两个阶段。"""
        result = generate_question_from_image(
            {
                "grading_mode": "exam",
                "image_path": "mock-question.jpg",
            }
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content_mode"], "stem_only")
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["score_source"], "default")
        self.assertTrue(result["is_answer_ai_generated"])
        self.assertTrue(result["is_scoring_paths_ai_generated"])
        self.assertEqual(result["model_call_count"], 2)
        self.assertTrue(any("默认值 10 分" in warning for warning in result["warnings"]))

    def test_image_solution_without_annotations_only_generates_scoring_paths(self) -> None:
        """验证图片已有作答时保留提取答案，仅让 AI 补齐评分路径。"""
        result = generate_question_from_image(
            {
                "grading_mode": "exam",
                "image_path": "mock-question.jpg",
                "mock_image_parse_output": {
                    "question_title": "加法计算",
                    "question_content": "计算 $1+1$。",
                    "question_type": "calculation",
                    "question_type_confidence": "high",
                    "detected_score": 6,
                    "content_mode": "solution_unannotated",
                    "standard_answers": ["$2$"],
                    "solution_text": "$1+1=2$。",
                    "scoring_paths": [],
                    "warnings": [],
                },
            }
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["standard_answers"], ["$2$"])
        self.assertFalse(result["is_answer_ai_generated"])
        self.assertTrue(result["is_scoring_paths_ai_generated"])
        self.assertEqual(result["model_call_count"], 2)
        self.assertEqual(sum(point["score"] for point in result["scoring_paths"][0]["points"]), 6)

    def test_image_annotated_solution_is_extracted_without_generation(self) -> None:
        """验证完整标注图片只调用一次视觉识别，不让 AI 改写答案或评分点。"""
        original_paths = [
            {
                "id": "teacher_path",
                "name": "老师标注解法",
                "reference_solution": "$1+1=2$。",
                "applicability": "直接计算。",
                "points": [
                    {"id": "teacher_point_1", "score": 3, "description": "写出计算式。"},
                    {"id": "teacher_point_2", "score": 3, "description": "结果正确。"},
                ],
            }
        ]
        result = generate_question_from_image(
            {
                "grading_mode": "exam",
                "image_path": "mock-question.jpg",
                "mock_image_parse_output": {
                    "question_title": "加法计算",
                    "question_content": "计算 $1+1$。",
                    "question_type": "calculation",
                    "question_type_confidence": "high",
                    "detected_score": 6,
                    "content_mode": "solution_annotated",
                    "standard_answers": ["$2$"],
                    "solution_text": "$1+1=2$。",
                    "scoring_paths": original_paths,
                    "warnings": [],
                },
            }
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["standard_answers"], ["$2$"])
        self.assertEqual(result["scoring_paths"], original_paths)
        self.assertFalse(result["is_answer_ai_generated"])
        self.assertFalse(result["is_scoring_paths_ai_generated"])
        self.assertEqual(result["model_call_count"], 1)

    def test_image_method_rejects_input_score(self) -> None:
        """验证图片方法不会误用外部分数覆盖截图识别与默认分数规则。"""
        result = generate_question_from_image(
            {
                "grading_mode": "exam",
                "image_path": "mock-question.jpg",
                "score": 20,
            }
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("不接收 score", result["error"]["message"])

    def test_v25_exam_entry_remains_available(self) -> None:
        """验证升级到 v2.6.1 后原有考试批改入口及其调用方式保持可用。"""
        sample_path = HANDOFF_ROOT / "ai_grading_service" / "sample_input.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))

        result = grade_answer(payload)

        self.assertEqual(__version__, "2.6.1")
        self.assertEqual(result["status"], "success")

    def test_grading_requires_confirmed_question_type(self) -> None:
        """验证正式批改仍要求传入老师确认后的题型字段。"""
        sample_path = HANDOFF_ROOT / "ai_grading_service" / "sample_input.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
        payload["question"].pop("question_type", None)
        payload["question"].get("rubric", {}).pop("type", None)

        result = grade_answer(payload)

        self.assertEqual(result["status"], "failed")
        self.assertIn("question.question_type", result["error"]["message"])

    def test_question_generation_prompts_are_registered(self) -> None:
        """验证 v2.6.1 的生成提示词和格式修复提示词可以从包内稳定加载。"""
        image_prompt = load_prompt("question_image_parser")
        generator_prompt = load_prompt("question_config_generator")
        formatter_prompt = load_prompt("question_output_formatter")

        self.assertIn("禁止自行补写", image_prompt)
        self.assertIn("allowed_generation_fields", generator_prompt)
        self.assertIn("$...$", image_prompt)
        self.assertIn("$...$", generator_prompt)
        self.assertIn("不含公式", formatter_prompt)

    def test_quality_validator_covers_all_math_display_fields(self) -> None:
        """验证标准答案、正文、评分路径和评分点中的裸公式都会被定位。"""
        output = {
            "question_title": "基础加法计算",
            "question_content": "计算 1+1。",
            "standard_answers": ["2"],
            "solution_text": "所以 1+1=2。",
            "warnings": [],
            "scoring_paths": [
                {
                    "name": "直接计算法",
                    "reference_solution": "计算 1+1=2。",
                    "applicability": "适用于 1+1。",
                    "points": [{"description": "得到 1+1=2。"}],
                }
            ],
        }

        issues = find_question_generation_quality_issues(output)

        issue_text = "；".join(issues)
        self.assertIn("question_content", issue_text)
        self.assertIn("standard_answers[0]", issue_text)
        self.assertIn("solution_text", issue_text)
        self.assertIn("reference_solution", issue_text)
        self.assertIn("points[0].description", issue_text)

    def test_text_method_repairs_latex_and_labels_once(self) -> None:
        """验证文本方法发现裸公式后只增加一次修复调用并返回合规内容。"""
        with patch(
            "ai_grading_service.question_generation_api.build_provider",
            return_value=RepairingQuestionProvider(),
        ):
            result = generate_question_from_text(
                {"grading_mode": "exam", "question_content": "计算 1+1。", "score": 10}
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["question_title"], "基础加法计算")
        self.assertEqual(result["standard_answers"], ["$2$"])
        self.assertEqual(result["model_call_count"], 2)
        self.assertEqual(result["model_stages"][-1]["stage_type"], "question_output_formatter")
        self.assertEqual(find_question_generation_quality_issues(result), [])

    def test_formula_label_uses_program_fallback_after_failed_label_repair(self) -> None:
        """验证名称字段仍含公式时可使用不含公式的程序兜底，而不改写正文。"""
        output = {
            "question_title": "求 $f(x)$ 的值",
            "question_type": "calculation",
            "scoring_paths": [{"name": "$f(x)$ 方法"}],
        }

        normalized_output = apply_formula_free_label_fallbacks(output)

        self.assertEqual(normalized_output["question_title"], "数学计算题")
        self.assertEqual(normalized_output["scoring_paths"][0]["name"], "解法 1")

        variable_title_output = apply_formula_free_label_fallbacks(
            {"question_title": "矩阵 A 高次幂计算", "question_type": "calculation", "scoring_paths": []}
        )
        self.assertEqual(variable_title_output["question_title"], "数学计算题")

    def test_formatter_cannot_change_scoring_structure(self) -> None:
        """验证格式修复模型改变评分点分值时，整个请求会被质量门拒绝。"""
        with patch(
            "ai_grading_service.question_generation_api.build_provider",
            return_value=StructureChangingQuestionProvider(),
        ):
            result = generate_question_from_text(
                {"grading_mode": "exam", "question_content": "计算 1+1。", "score": 10}
            )

        self.assertEqual(result["status"], "failed")
        self.assertIn("改变了答案数量、评分结构或业务状态", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
