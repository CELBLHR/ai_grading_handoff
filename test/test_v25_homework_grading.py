from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    sys.path.insert(0, str(HANDOFF_ROOT))

os.environ["AI_PIPELINE_PROVIDER"] = "mock"

from ai_grading_service import (  # noqa: E402
    analyze_answer_method,
    extract_visual_answer,
    grade_answer,
    grade_homework_answer,
    grade_homework_answers,
)
from app.pipelines.contracts import validate_stage_output  # noqa: E402
from app.prompts.loader import load_homework_review_prompt  # noqa: E402


class HomeworkGradingTest(unittest.TestCase):
    """验证 v2.5 作业评价入口、复核模式和考试链路兼容性。"""

    def setUp(self) -> None:
        """
        功能：构造不包含满分和 rubric 的作业单题样例。

        参数：
            无

        返回：
            None
        """
        sample_path = HANDOFF_ROOT / "ai_grading_service" / "sample_input.json"
        self.exam_payload = json.loads(sample_path.read_text(encoding="utf-8"))
        self.homework_payload = json.loads(json.dumps(self.exam_payload, ensure_ascii=False))
        self.homework_payload["question"].pop("max_score", None)
        self.homework_payload["question"].pop("rubric", None)

    def test_homework_grading_does_not_require_score_or_rubric(self) -> None:
        """验证作业评价不要求考试满分和评分细则。"""
        with patch.dict(os.environ, {"HOMEWORK_REVIEW_MODE": "off"}):
            result = grade_homework_answer(self.homework_payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["grading_mode"], "homework")
        self.assertEqual(result["question_type"], "calculation")
        self.assertIsNone(result["suggested_score"])
        self.assertIsNone(result["final_score"])
        self.assertEqual(result["score_points"], [])
        self.assertEqual(result["deductions"], [])
        self.assertEqual(result["homework_feedback"]["answer_status"], "correct")
        self.assertEqual(result["homework_review_status"], "not_run")
        self.assertIsNone(result["stage_outputs"]["homework_review_decision"])
        self.assertEqual(
            [stage["stage_type"] for stage in result["stage_results"]],
            ["visual_ocr", "evidence_risk", "method_analysis", "homework_review"],
        )

    def test_split_results_can_be_reused_for_homework_grading(self) -> None:
        """验证无满分作业可以复用视觉、证据风险和方法分析结果。"""
        visual_result = extract_visual_answer(self.homework_payload)
        method_result = analyze_answer_method(visual_result)
        with patch.dict(os.environ, {"HOMEWORK_REVIEW_MODE": "off"}):
            result = grade_homework_answer(method_result)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [stage["prompt_version"] for stage in result["stage_results"][:3]],
            ["reused", "reused", "reused"],
        )
        self.assertEqual(result["stage_results"][3]["stage_type"], "homework_review")

    def test_exam_grading_still_requires_max_score(self) -> None:
        """验证放宽作业输入不会改变 grade_answer 的考试输入契约。"""
        invalid_exam_payload = json.loads(json.dumps(self.exam_payload, ensure_ascii=False))
        invalid_exam_payload["question"].pop("max_score")

        result = grade_answer(invalid_exam_payload)

        self.assertEqual(result["status"], "failed")
        self.assertIn("question.max_score", result["error"]["message"])

    def test_shadow_mode_runs_review_without_overriding_business_status(self) -> None:
        """验证 shadow 模式运行独立复核并明确标记为隐藏观察状态。"""
        with patch.dict(os.environ, {"HOMEWORK_REVIEW_MODE": "shadow"}):
            result = grade_homework_answer(self.homework_payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["homework_review_status"], "shadow")
        self.assertIsInstance(result["stage_outputs"]["homework_review_decision"], dict)
        self.assertEqual(result["stage_results"][-1]["stage_type"], "homework_review_decision")

    def test_visible_mode_exposes_review_decision(self) -> None:
        """验证 visible 模式完成独立复核并作为正式可信度来源。"""
        with patch.dict(os.environ, {"HOMEWORK_REVIEW_MODE": "visible"}):
            result = grade_homework_answer(self.homework_payload)

        decision_output = result["stage_outputs"]["homework_review_decision"]
        self.assertEqual(result["homework_review_status"], "visible")
        self.assertEqual(result["review_advice"], decision_output["review_advice"])
        self.assertEqual(result["confidence_level"], decision_output["confidence_level"])

    def test_homework_grading_supports_cancellation(self) -> None:
        """验证作业评价在模型调用前能够返回稳定取消结构。"""
        result = grade_homework_answer(self.homework_payload, cancel_checker=lambda: True)

        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["homework_feedback"]["answer_status"], "unclear")

    def test_homework_grading_discards_result_when_cancelled_during_call(self) -> None:
        """验证最后一次模型调用期间取消时不会继续交付作业评价。"""
        check_results = iter([False, False, False, False, False, True])
        with patch.dict(os.environ, {"HOMEWORK_REVIEW_MODE": "off"}):
            result = grade_homework_answer(
                self.homework_payload,
                cancel_checker=lambda: next(check_results),
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertIn("homework_review", result["stage_outputs"])

    def test_homework_batch_keeps_input_order(self) -> None:
        """验证顺序批量辅助函数保持输入和输出顺序一致。"""
        second_payload = json.loads(json.dumps(self.homework_payload, ensure_ascii=False))
        second_payload["answer_item_id"] = "answer_demo_002"
        with patch.dict(os.environ, {"HOMEWORK_REVIEW_MODE": "off"}):
            results = grade_homework_answers([self.homework_payload, second_payload])

        self.assertEqual([result["answer_item_id"] for result in results], ["answer_demo_001", "answer_demo_002"])

    def test_homework_contract_rejects_score_fields(self) -> None:
        """验证作业评价模型不能把考试分数字段混入结果。"""
        output = {
            "answer_status": "correct",
            "summary": "作答正确。",
            "strengths": [],
            "issues": [],
            "improvement_suggestions": [],
            "confidence_level": "high",
            "review_advice": "auto_accept",
            "risk_tags": [],
            "review_focus": [],
            "evidence_safe": True,
            "suggested_score": 10,
        }

        with self.assertRaisesRegex(ValueError, "不得输出分数"):
            validate_stage_output("homework_review", output)

    def test_homework_prompt_uses_question_type_rule_pack(self) -> None:
        """验证作业评价会按题型加载专用规则而不是考试评分规则。"""
        prompt_text = load_homework_review_prompt("fill_blank")

        self.assertIn("# Fill Blank Rules", prompt_text)
        self.assertIn("禁止输出 `suggested_score`", prompt_text)


if __name__ == "__main__":
    unittest.main()
