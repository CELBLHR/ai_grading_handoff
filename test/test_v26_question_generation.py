from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path


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


class QuestionGenerationTest(unittest.TestCase):
    """验证 v2.6 两个题目配置生成入口及 v2.5 批改兼容性。"""

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
        """验证升级到 v2.6 后原有考试批改入口及其调用方式保持可用。"""
        sample_path = HANDOFF_ROOT / "ai_grading_service" / "sample_input.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))

        result = grade_answer(payload)

        self.assertEqual(__version__, "2.6")
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
        """验证 v2.6 两个新增提示词可以从包内稳定加载。"""
        image_prompt = load_prompt("question_image_parser")
        generator_prompt = load_prompt("question_config_generator")

        self.assertIn("禁止自行补写", image_prompt)
        self.assertIn("allowed_generation_fields", generator_prompt)


if __name__ == "__main__":
    unittest.main()
