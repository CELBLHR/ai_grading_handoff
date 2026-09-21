from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    sys.path.insert(0, str(HANDOFF_ROOT))

os.environ["AI_GRADING_PROVIDER"] = "mock"

from ai_grading_service import (  # noqa: E402
    analyze_answer_method,
    discover_method_clusters,
    extract_visual_answer,
    grade_answer,
)
from ai_grading_service.demo_group_images_to_clusters import process_image_group  # noqa: E402
from app.pipelines.stages.review_decision import enforce_unmatched_scoring_path_review  # noqa: E402
from app.pipelines.stages.scoring import (  # noqa: E402
    get_active_scoring_points,
    validate_score_points_against_rubric,
    validate_scoring_path_selection,
)


class SplitPipelineTest(unittest.TestCase):
    """验证 v2.4 拆分链路与旧批改入口可以并行使用。"""

    def setUp(self) -> None:
        sample_path = HANDOFF_ROOT / "ai_grading_service" / "sample_input.json"
        self.payload = json.loads(sample_path.read_text(encoding="utf-8"))
        cluster_sample_path = HANDOFF_ROOT / "ai_grading_service" / "sample_method_cluster_input.json"
        self.cluster_payload = json.loads(cluster_sample_path.read_text(encoding="utf-8"))

    def test_split_pipeline_and_reused_grading(self) -> None:
        visual_result = extract_visual_answer(self.payload)
        self.assertEqual(visual_result["status"], "success")

        method_result = analyze_answer_method(visual_result)
        self.assertEqual(method_result["status"], "success")
        self.assertTrue(method_result["method_summary"])
        self.assertEqual(method_result["stage_results"][0]["prompt_version"], "reused")

        grading_result = grade_answer(method_result)
        self.assertEqual(grading_result["status"], "success")
        self.assertEqual(
            [item["prompt_version"] for item in grading_result["stage_results"][:3]],
            ["reused", "reused", "reused"],
        )
        self.assertEqual(grading_result["stage_results"][3]["stage_type"], "scoring")

        rubric = self.payload["question"]["rubric"]
        cluster_result = discover_method_clusters(
            {
                "project_id": self.payload["project_id"],
                "question": self.payload["question"],
                "current_rubric": rubric,
                "method_pool": [method_result],
                "threshold": {"min_cluster_count": 1},
            }
        )
        self.assertEqual(cluster_result["status"], "success")
        self.assertEqual(cluster_result["current_rubric"], rubric)
        self.assertTrue(cluster_result["method_clusters"])

    def test_legacy_one_step_grading(self) -> None:
        result = grade_answer(self.payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["stage_results"]), 5)

    def test_multiple_scoring_paths_select_exactly_one_path(self) -> None:
        payload = json.loads(json.dumps(self.payload, ensure_ascii=False))
        payload["question"]["rubric"] = {
            "type": "calculation",
            "scoring_paths": [
                {
                    "id": "direct_addition",
                    "name": "直接计算",
                    "points": [
                        {"id": "direct_process", "description": "写出加法过程", "score": 1},
                        {"id": "direct_result", "description": "得到结果 2", "score": 1},
                    ],
                },
                {
                    "id": "counting",
                    "name": "计数法",
                    "points": [
                        {"id": "counting_model", "description": "建立计数模型", "score": 1},
                        {"id": "counting_result", "description": "得到结果 2", "score": 1},
                    ],
                },
            ],
        }

        result = grade_answer(payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["selected_path_id"], "direct_addition")
        self.assertEqual(
            {point["point_id"] for point in result["score_points"]},
            {"direct_process", "direct_result"},
        )

    def test_cross_path_score_point_is_rejected(self) -> None:
        question = {
            "rubric": {
                "scoring_paths": [
                    {"id": "path_a", "points": [{"id": "a1", "score": 2}]},
                    {"id": "path_b", "points": [{"id": "b1", "score": 2}]},
                ]
            }
        }
        scoring_output = {
            "selected_path_id": "path_a",
            "suggested_score": 2,
            "score_points": [{"point_id": "b1", "score": 2}],
        }

        validate_scoring_path_selection(question, scoring_output)
        active_points = get_active_scoring_points(question, scoring_output)
        with self.assertRaisesRegex(ValueError, "未在当前生效评分点中声明"):
            validate_score_points_against_rubric(active_points, scoring_output)

    def test_unmatched_path_forces_required_review(self) -> None:
        question = {
            "rubric": {
                "scoring_paths": [
                    {"id": "path_a", "points": [{"id": "a1", "score": 2}]},
                ]
            }
        }
        scoring_output = {"selected_path_id": None, "suggested_score": 0, "score_points": []}
        review_output = {
            "confidence_level": "high",
            "review_advice": "auto_accept",
            "risk_tags": [],
            "review_focus": [],
            "evidence_safe": True,
            "policy_overrides": [],
        }

        result = enforce_unmatched_scoring_path_review(question, scoring_output, review_output)

        self.assertEqual(result["review_advice"], "required_review")
        self.assertFalse(result["evidence_safe"])
        self.assertIn("unmatched_scoring_path", result["risk_tags"])

    def test_split_functions_support_pre_call_cancellation(self) -> None:
        """验证视觉和方法分析入口都会在模型调用前响应取消。"""
        visual_result = extract_visual_answer(self.payload, cancel_checker=lambda: True)
        method_result = analyze_answer_method(self.payload, cancel_checker=lambda: True)

        self.assertEqual(visual_result["status"], "cancelled")
        self.assertEqual(method_result["status"], "cancelled")

    def test_method_cluster_supports_pre_call_cancellation(self) -> None:
        """验证聚合任务已取消时不会继续调用聚合模型。"""
        result = discover_method_clusters(self.cluster_payload, cancel_checker=lambda: True)

        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["current_rubric"], self.cluster_payload["current_rubric"])
        self.assertEqual(result["method_clusters"], [])

    def test_method_cluster_discards_result_when_cancelled_during_call(self) -> None:
        """验证模型调用期间发生取消时，模型返回结果不会被对接方误用。"""
        check_results = iter([False, True])
        result = discover_method_clusters(
            self.cluster_payload,
            cancel_checker=lambda: next(check_results),
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["error"]["code"], "CANCELLED")
        self.assertEqual(result["raw_output"], {})

    def test_group_images_can_flow_into_method_clusters(self) -> None:
        """验证同题图片组能够依次完成视觉、方法分析和方法聚合。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_dir = Path(temp_dir)
            first_image = manifest_dir / "answer_001.jpg"
            second_image = manifest_dir / "answer_002.jpg"
            # Mock provider 不读取图像内容，只需要文件存在即可验证路径解析和完整编排。
            first_image.write_bytes(b"mock-image-1")
            second_image.write_bytes(b"mock-image-2")
            manifest = {
                "project_id": "project_group_test",
                "run_id": "group_test_run",
                "question": self.payload["question"],
                "current_rubric": self.payload["question"]["rubric"],
                "threshold": {"min_cluster_count": 2},
                "answers": [
                    {
                        "answer_item_id": "group_answer_001",
                        "student": {"student_no": "20260001"},
                        "image_path": first_image.name,
                    },
                    {
                        "answer_item_id": "group_answer_002",
                        "student": {"student_no": "20260002"},
                        "image_path": second_image.name,
                    },
                ],
            }

            result = process_image_group(manifest, manifest_dir)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_images"], 2)
        self.assertEqual(result["method_success_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["cluster_result"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
