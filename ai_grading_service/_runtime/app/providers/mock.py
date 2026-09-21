from __future__ import annotations

from typing import Any


def build_mock_score_points_from_rubric(
    question: dict[str, Any],
    suggested_score: float,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    功能：根据输入 rubric 生成 mock 得分点，保证本地联调也遵守原始评分点 id。

    参数：
        question (dict[str, Any]): 当前题目信息，通常包含 rubric.scoring_points。
        suggested_score (float): mock provider 计算出的建议总分。

    返回：
        tuple[list[dict[str, Any]], str | None]: mock 得分点和选中的路径 ID。
    """
    rubric = question.get("rubric") if isinstance(question, dict) else {}
    if not isinstance(rubric, dict):
        return [], None

    selected_path_id: str | None = None
    scoring_paths = rubric.get("scoring_paths")
    if isinstance(scoring_paths, list) and scoring_paths:
        selected_path = next((path for path in scoring_paths if isinstance(path, dict)), {})
        selected_path_id = str(selected_path.get("id") or "").strip() or None
        scoring_points = selected_path.get("points")
    else:
        scoring_points = rubric.get("scoring_points")
    if not isinstance(scoring_points, list):
        return [], selected_path_id

    remaining_score = float(suggested_score)
    mock_points: list[dict[str, Any]] = []
    for point in scoring_points:
        if not isinstance(point, dict):
            continue

        point_id = str(point.get("id", "")).strip()
        if not point_id:
            continue

        max_point_score = float(point.get("score") or 0)
        awarded_score = max_point_score if remaining_score >= max_point_score else 0.0
        remaining_score -= awarded_score
        mock_points.append(
            {
                "point_id": point_id,
                "score": awarded_score,
                "rationale": "mock 按输入 rubric 原始 id 回填得分，用于验证严格评分点对齐逻辑。",
            }
        )

    return mock_points, selected_path_id


def build_legacy_mock_score_points(question_no: str, suggested_score: float, max_score: float) -> list[dict[str, Any]]:
    """
    功能：在外部未提供 rubric.scoring_points 时生成兼容旧格式的 mock 得分点。

    参数：
        question_no (str): 当前题号，用于构造本地演示 id。
        suggested_score (float): mock provider 计算出的建议总分。
        max_score (float): 当前题目满分。

    返回：
        list[dict[str, Any]]: 兼容旧输入格式的 mock 得分点。
    """
    return [
        {
            "id": f"{question_no}-1",
            "description": "关键过程",
            "status": "partially_achieved",
            "score": max(suggested_score - 2, 0),
            "max_score": max(max_score - 2, 0),
        },
        {
            "id": f"{question_no}-2",
            "description": "最终答案",
            "status": "achieved",
            "score": min(2, suggested_score),
            "max_score": min(2, max_score),
        },
    ]


class MockModelProvider:
    """
    功能：提供 Step 3A 使用的 mock 模型输出，先打通正式 backend 流水线结构。

    参数：
        无。

    返回：
        MockModelProvider: mock provider 实例。
    """

    model_name = "mock-model"

    def run_question_image_parser(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        功能：生成可覆盖三种图片内容场景的 mock 识别结果。

        参数：
            context (dict[str, Any]): 题目图片生成上下文；测试可通过 mock_image_parse_output 覆盖默认结果。

        返回：
            dict[str, Any]: 仅包含图片中已识别内容的结构化结果。
        """
        supplied_output = context.get("mock_image_parse_output")
        if isinstance(supplied_output, dict):
            # 允许测试精确声明图片中“实际看见”的内容，避免 mock 自行混入生成行为。
            return supplied_output

        return {
            "question_title": "基础加法计算",
            "question_content": "计算 $1+1$。",
            "question_type": "calculation",
            "question_type_confidence": "high",
            "detected_score": None,
            "content_mode": "stem_only",
            "standard_answers": [],
            "solution_text": "",
            "scoring_paths": [],
            "warnings": [],
        }

    def run_question_config_generator(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        功能：按照允许生成字段构造稳定的 mock 题目配置。

        参数：
            context (dict[str, Any]): 包含题干、已有内容、业务类型和允许生成字段的上下文。

        返回：
            dict[str, Any]: 可通过 v2.6.1 题目生成契约校验的 mock 结果。
        """
        question = context.get("question", {})
        existing_content = context.get("existing_content", {})
        allowed_fields = set(context.get("allowed_generation_fields", []))
        max_score = question.get("score")

        standard_answers = list(existing_content.get("standard_answers") or [])
        if "standard_answers" in allowed_fields:
            standard_answers = ["$2$"]

        scoring_paths = list(existing_content.get("scoring_paths") or [])
        if "scoring_paths" in allowed_fields and max_score is not None:
            numeric_score = float(max_score)
            first_score = round(numeric_score * 0.6, 2)
            second_score = round(numeric_score - first_score, 2)
            scoring_paths = [
                {
                    "id": "path_1",
                    "name": "直接计算",
                    "reference_solution": "直接完成计算并写出结论。",
                    "applicability": "适用于直接计算或等价正确方法。",
                    "points": [
                        {
                            "id": "path_1_point_1",
                            "score": first_score,
                            "description": "正确建立计算过程。",
                        },
                        {
                            "id": "path_1_point_2",
                            "score": second_score,
                            "description": "得到正确最终答案。",
                        },
                    ],
                }
            ]

        raw_question_type = str(question.get("question_type") or "").strip()
        inferred_question_type = "calculation" if raw_question_type in {"", "unknown"} else raw_question_type
        raw_confidence = str(question.get("question_type_confidence") or "").strip()
        inferred_confidence = "high" if raw_question_type in {"", "unknown"} else raw_confidence or "high"
        return {
            "question_title": str(question.get("question_title") or "基础加法计算"),
            "question_type": inferred_question_type,
            "question_type_confidence": inferred_confidence,
            "standard_answers": standard_answers,
            "solution_text": "计算可得 $1+1=2$。" if "standard_answers" in allowed_fields else str(existing_content.get("solution_text") or ""),
            "scoring_paths": scoring_paths,
            "warnings": [],
        }

    def run_question_output_formatter(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        功能：在 mock 模式返回原始最终响应，用于验证未修复内容会被质量门拒绝。

        参数：
            context (dict[str, Any]): 包含 original_output 的格式修复上下文。

        返回：
            dict[str, Any]: 原始最终响应的浅层副本。
        """
        # mock 不猜测数学语义；正常 mock 数据本身应合规，故只有异常测试数据会走到这里并被二次校验拒绝。
        return dict(context.get("original_output") or {})

    def get_stage_model_name(self, stage_type: str) -> str:
        """
        功能：返回 mock provider 在指定阶段使用的模型名称。

        参数：
            stage_type (str): 流水线阶段类型。

        返回：
            str: mock 阶段统一使用的模型名称。
        """
        # mock 模式不区分视觉、批改和闸门模型；保留统一名称便于本地演示。
        return self.model_name

    def run_visual_ocr(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        功能：生成模拟视觉/OCR 输出。

        参数：
            context (dict[str, Any]): 单个答题项上下文。

        返回：
            dict[str, Any]: mock 视觉输出。
        """
        item = context["answer_item"]
        image = context["answer_image"]
        image_path = image.get("file_path", "")
        image_url = image.get("image_url", "")

        return {
            "transcription": {
                "student_work": {
                    "blocks": [
                        {
                            "block_id": "block_1",
                            "type": "normal",
                            "content": f"{item['student_name']} 第 {item['question_no']} 题 mock OCR 内容",
                            "confidence": "medium",
                            "usable_for_grading": "yes",
                        }
                    ]
                },
                "visual_summary": {
                    "overall_visual_risk": "medium",
                    "summary": "这是 Step 3A 的 mock 视觉结果，真实模型将在 Step 3B 接入。",
                },
            },
            "normalized_input": {
                "answer_item_id": item["id"],
                "question_no": item["question_no"],
                "image_path": image_path,
                "image_url": image_url,
                "student_answer_text": f"{item['student_name']} 第 {item['question_no']} 题 mock OCR 内容",
            },
            "ocr_risk": {"level": "medium", "types": ["mock_uncertainty"]},
        }

    def run_evidence_risk(self, context: dict[str, Any], visual_output: dict[str, Any]) -> dict[str, Any]:
        visual_risk = visual_output.get("ocr_risk", {})
        risk_level = str(visual_risk.get("level") or "medium")
        risk_tags = [] if risk_level == "low" else ["mock_ocr_uncertainty"]
        risk_locations = [] if risk_level == "low" else [{"block_id": "block_1", "reason": "mock OCR 置信度中等"}]
        return {
            "evidence_risk_level": risk_level,
            "risk_tags": risk_tags,
            "risk_locations": risk_locations,
            "uncertain_symbols": [],
            "consistency_risks": [],
            "final_answer_risks": [],
            "grading_constraints": [] if risk_level == "low" else ["可自动处理，但应保留 OCR 风险审计。"],
        }

    def run_method_analysis(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "method_summary": "mock 解法摘要：学生写出单一路线的高层作答过程。",
            "method_confidence": "medium",
            "method_evidence_blocks": ["block_1"],
        }

    def run_scoring(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any]:
        """
        功能：生成模拟 AI 批改输出。

        参数：
            context (dict[str, Any]): 单个答题项上下文。
            visual_output (dict[str, Any]): 视觉阶段输出。

        返回：
            dict[str, Any]: mock 批改输出。
        """
        item = context["answer_item"]
        max_score = float(item["max_score"])
        question_prefix = str(item["question_no"]).split(".")[0]
        question_number = int(question_prefix) if question_prefix.isdigit() else 1
        student_tail = sum(ord(char) for char in item["student_no"]) % 3
        suggested_score = max(0.0, min(max_score, max_score - ((question_number + student_tail) % 3)))
        score_points, selected_path_id = build_mock_score_points_from_rubric(
            question=context.get("question", {}),
            suggested_score=suggested_score,
        )
        if score_points:
            suggested_score = sum(float(point["score"]) for point in score_points)
        else:
            score_points = build_legacy_mock_score_points(
                question_no=item["question_no"],
                suggested_score=suggested_score,
                max_score=max_score,
            )

        return {
            "selected_path_id": selected_path_id,
            "suggested_score": suggested_score,
            "max_score": max_score,
            "summary": "Step 3A mock 批改结果，用于验证任务、阶段和结果表写入。",
            "score_points": score_points,
            "deductions": [],
            "visual_reference": visual_output.get("normalized_input", {}),
            "mathematical_review": {
                "method_validity": "partially_valid",
                "logic_status": "partial",
                "final_answer_equivalence": "equivalent" if suggested_score >= max_score - 0.5 else "not_equivalent",
            },
            "evidence_usage": {
                "used_evidence_risks": evidence_risk_output.get("risk_tags", []),
                "uncertain_but_scored_conservatively": [],
            },
            "final_answer_status": "correct" if suggested_score >= max_score - 0.5 else "incorrect",
            "method_summary": method_analysis_output.get("method_summary", ""),
        }

    def run_review_decision(
        self,
        context: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        scoring_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any]:
        """
        功能：生成模拟证据安全闸门输出。

        参数：
            context (dict[str, Any]): 单个答题项上下文。
            visual_output (dict[str, Any]): 视觉阶段输出。
            scoring_output (dict[str, Any]): 评分阶段输出。

        返回：
            dict[str, Any]: mock 闸门输出。
        """
        max_score = float(scoring_output["max_score"])
        suggested_score = float(scoring_output["suggested_score"])

        if suggested_score >= max_score - 0.5:
            confidence_level = "high"
            review_advice = "auto_accept"
            risk_tags: list[str] = []
        elif suggested_score >= max_score * 0.55:
            confidence_level = "medium"
            review_advice = "sample_review"
            risk_tags = ["mock_ocr_uncertainty"]
        else:
            confidence_level = "low"
            review_advice = "required_review"
            risk_tags = ["mock_missing_key_step", "mock_high_impact_symbol_risk"]

        return {
            "confidence_level": confidence_level,
            "review_advice": review_advice,
            "risk_tags": risk_tags,
            "review_focus": ["核对原图、关键步骤和最终答案是否一致。"],
            "evidence_safe": review_advice == "auto_accept",
            "decision_reason": "mock 复核决策，用于验证 V2 当前生效结果表写入。",
            "policy_overrides": [],
            "evidence_risk_reference": evidence_risk_output,
        }

    def run_homework_review(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any]:
        """
        功能：生成不含分数的 mock 作业评价，供 v2.5 本地联调和测试使用。

        参数：
            context (dict[str, Any]): 当前单题上下文。
            visual_output (dict[str, Any]): 视觉或文本规范化结果。
            evidence_risk_output (dict[str, Any]): 证据风险结果。
            method_analysis_output (dict[str, Any]): 学生解法分析结果。

        返回：
            dict[str, Any]: 符合作业评价契约的 mock 输出。
        """
        normalized_input = visual_output.get("normalized_input", {})
        answer_text = str(normalized_input.get("student_answer_text") or "").strip()
        standard_answer = str(context.get("question", {}).get("standard_answer") or "").strip()
        normalized_answer = "".join(answer_text.split())
        normalized_standard = "".join(standard_answer.split())

        if not answer_text:
            answer_status = "blank"
            summary = "未看到可评价的学生作答。"
            strengths: list[str] = []
            issues = [
                {
                    "type": "missing_step",
                    "location": "整份作答",
                    "evidence": "未识别到有效作答内容。",
                    "explanation": "当前没有足够内容判断学生的解题过程。",
                    "correction_guidance": "请补充完整作答后重新提交。",
                }
            ]
        elif normalized_standard and normalized_standard in normalized_answer:
            answer_status = "correct"
            summary = "作答过程与结论和参考答案一致，当前未发现明确错误。"
            strengths = ["能够给出与参考答案一致的过程或结论。"]
            issues = []
        else:
            answer_status = "partially_correct"
            summary = "已看到明确作答，但仍需要结合具体步骤订正或补充。"
            strengths = ["已经尝试使用明确的解题路线完成本题。"]
            issues = [
                {
                    "type": "final_answer",
                    "location": "作答结论",
                    "evidence": answer_text,
                    "explanation": "mock 模式无法确认该结论与参考答案完全等价。",
                    "correction_guidance": "请核对关键计算和最终结论；真实环境将由作业评价模型给出具体建议。",
                }
            ]

        risk_level = str(evidence_risk_output.get("evidence_risk_level") or "medium")
        confidence_level = "high" if risk_level == "low" else "medium" if risk_level == "medium" else "low"
        review_advice = "auto_accept" if risk_level == "low" else "sample_review" if risk_level == "medium" else "required_review"
        return {
            "answer_status": answer_status,
            "summary": summary,
            "strengths": strengths,
            "issues": issues,
            "improvement_suggestions": [] if answer_status == "correct" else ["根据问题定位逐步订正并再次核对结论。"],
            "confidence_level": confidence_level,
            "review_advice": review_advice,
            "risk_tags": evidence_risk_output.get("risk_tags", []),
            "review_focus": [
                str(item.get("reason") or item) if isinstance(item, dict) else str(item)
                for item in evidence_risk_output.get("risk_locations", [])
            ],
            "evidence_safe": risk_level == "low",
            "decision_reason": "根据当前证据风险生成的作业评价初步可信度。",
            "evidence_usage": {
                "used_evidence_risks": evidence_risk_output.get("risk_tags", []),
                "uncertain_content": evidence_risk_output.get("uncertain_symbols", []),
            },
            "method_summary": method_analysis_output.get("method_summary", ""),
        }

    def run_homework_review_decision(
        self,
        context: dict[str, Any],
        evidence_risk_output: dict[str, Any],
        homework_review_output: dict[str, Any],
        method_analysis_output: dict[str, Any],
    ) -> dict[str, Any]:
        """
        功能：生成 mock 作业评价复核结论，验证 shadow 和 visible 模式编排。

        参数：
            context (dict[str, Any]): 当前单题上下文。
            evidence_risk_output (dict[str, Any]): 证据风险结果。
            homework_review_output (dict[str, Any]): 作业评价结果。
            method_analysis_output (dict[str, Any]): 学生解法分析结果。

        返回：
            dict[str, Any]: 与通用复核字段兼容的 mock 决策结果。
        """
        risk_level = str(evidence_risk_output.get("evidence_risk_level") or "medium")
        confidence_level = "high" if risk_level == "low" else "medium" if risk_level == "medium" else "low"
        review_advice = "auto_accept" if risk_level == "low" else "sample_review" if risk_level == "medium" else "required_review"
        return {
            "confidence_level": confidence_level,
            "review_advice": review_advice,
            "risk_tags": evidence_risk_output.get("risk_tags", []),
            "review_focus": ["核对证据风险是否影响作业评价中的错误定位。"] if risk_level != "low" else [],
            "evidence_safe": risk_level == "low",
            "decision_reason": "mock 作业评价复核结果。",
            "policy_overrides": [],
        }

    def run_rubric_gap_discovery(self, context: dict[str, Any]) -> dict[str, Any]:
        summaries = context.get("summary_pool", [])
        clusters: dict[str, dict[str, Any]] = {}
        for row in summaries:
            if not isinstance(row, dict):
                continue
            summary = str(row.get("method_summary") or "").strip()
            if not summary:
                continue
            if "分部积分" in summary:
                cluster_name = "分部积分法"
            elif "换元" in summary:
                cluster_name = "换元法"
            elif "通项" in summary or "敛散" in summary:
                cluster_name = "通项判别法"
            else:
                cluster_name = "其他高频解法"

            cluster = clusters.setdefault(
                cluster_name,
                {
                    "cluster_name": cluster_name,
                    "count": 0,
                    "representative_answer_item_ids": [],
                    "representative_summaries": [],
                    "reason": "mock 按摘要关键词归并，用于本地演示 Phase2 流程。",
                },
            )
            cluster["count"] += 1
            if len(cluster["representative_answer_item_ids"]) < 5:
                cluster["representative_answer_item_ids"].append(str(row.get("answer_item_id") or ""))
                cluster["representative_summaries"].append(summary)

        rubric_text = str(context.get("rubric") or "")
        max_score = float(context.get("question", {}).get("max_score") or 0)
        gap_candidates = []
        excluded_candidates = []
        for cluster in clusters.values():
            cluster_name = cluster["cluster_name"]
            coverage_status = "covered" if cluster_name and cluster_name in rubric_text else "not_covered"
            if coverage_status == "covered":
                excluded_candidates.append(
                    {
                        "candidate_type": "covered_solution",
                        "method": cluster_name,
                        "count": cluster["count"],
                        "representative_answer_item_ids": cluster["representative_answer_item_ids"],
                        "representative_summaries": cluster["representative_summaries"],
                        "reason": "mock 判断当前 rubric 文本已包含该解法名称。",
                    }
                )
                continue

            first_score = round(max_score * 0.45, 2) if max_score else 1
            second_score = round(max(max_score - first_score, 0), 2) if max_score else 1
            gap_candidates.append(
                {
                    "candidate_type": "valid_new_solution",
                    "method": cluster_name,
                    "count": cluster["count"],
                    "coverage_status": "not_covered",
                    "representative_answer_item_ids": cluster["representative_answer_item_ids"],
                    "representative_summaries": cluster["representative_summaries"],
                    "reason": "mock 判断该解法是当前 rubric 未覆盖的可行新增解法。",
                    "scoring_path_draft": {
                        "id": f"candidate_path_{len(gap_candidates) + 1}",
                        "path_name": cluster_name,
                        "applicability": f"学生采用{cluster_name}或等价正确思路完成本题。",
                        "points": [
                            {
                                "id": f"candidate_path_{len(gap_candidates) + 1}_point_1",
                                "description": f"建立{cluster_name}的关键转化或核心思路。",
                                "score": first_score,
                                "required_evidence": "作答中能看到该方法的关键定义、代换、变形或判别依据。",
                                "partial_credit_rule": "该评分点未完整命中时给 0 分，不给部分分。",
                                "notes_for_ai": "核对该步骤是否是正确解法的一部分，而不是常见错误。",
                            },
                            {
                                "id": f"candidate_path_{len(gap_candidates) + 1}_point_2",
                                "description": "沿该路径完成后续计算并得到正确结论。",
                                "score": second_score,
                                "required_evidence": "作答中能看到完整计算过程、边界处理和最终结果。",
                                "partial_credit_rule": "该评分点未完整命中时给 0 分，不给部分分。",
                                "notes_for_ai": "最终结果需与标准答案等价。",
                            },
                        ],
                        "notes_for_ai": "mock 评分路径草稿，仅用于本地演示；正式环境由模型生成。",
                        "why_not_covered_by_current_rubric": "mock 判断当前 rubric 文本未包含该解法名称。",
                    },
                }
            )

        return {
            "method_clusters": list(clusters.values()),
            "gap_candidates": gap_candidates,
            "excluded_candidates": excluded_candidates,
        }

    def run_grading(self, context: dict[str, Any], visual_output: dict[str, Any]) -> dict[str, Any]:
        return self.run_scoring(
            context,
            visual_output,
            self.run_evidence_risk(context, visual_output),
            {
                "method_summary": "",
                "method_confidence": "low",
                "method_evidence_blocks": [],
            },
        )

    def run_risk_gate(
        self,
        context: dict[str, Any],
        visual_output: dict[str, Any],
        grading_output: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_risk_output = self.run_evidence_risk(context, visual_output)
        return self.run_review_decision(
            context,
            evidence_risk_output,
            grading_output,
            {
                "method_summary": "",
                "method_confidence": "low",
                "method_evidence_blocks": [],
            },
        )
