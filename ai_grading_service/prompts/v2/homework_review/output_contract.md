# Output JSON

仅输出合法 JSON：

{
  "answer_status": "correct | partially_correct | incorrect | incomplete | blank | unclear",
  "summary": "面向当前学生、概括本题作答质量的总体评价",
  "strengths": ["有卷面证据支持的正确部分"],
  "issues": [
    {
      "type": "conceptual | calculation | logic | notation | missing_step | final_answer",
      "location": "问题所在步骤、结论、公式、block 或区域",
      "evidence": "学生作答中实际可见的相关内容",
      "explanation": "问题的数学原因",
      "correction_guidance": "可执行的订正方向"
    }
  ],
  "improvement_suggestions": ["本题订正或后续练习建议"],
  "confidence_level": "high | medium | low",
  "review_advice": "auto_accept | sample_review | required_review | second_review",
  "risk_tags": [],
  "review_focus": [],
  "evidence_safe": true,
  "decision_reason": "当前评价为什么可以或不可以直接采用",
  "evidence_usage": {
    "used_evidence_risks": [],
    "uncertain_content": []
  }
}

# Output Discipline

- 禁止输出 `suggested_score`、`max_score`、`final_score`、`score_points`、`deductions` 或 `selected_path_id`。
- `strengths`、`improvement_suggestions`、`risk_tags` 和 `review_focus` 必须是字符串数组。
- `issues` 没有内容时必须为 `[]`，不得使用字符串、对象或 null 代替。
- 学生答案明确错误但证据清楚时，可以 `review_advice = "auto_accept"`；不要把答错等同于需要教师复核。
- 只有 OCR、符号、涂改、版面或证据不确定性可能改变评价时，才使用 `required_review`。
- `required_review` 时 `review_focus` 必须指出需要核对的具体位置。
- 输出前检查所有公式都位于 `$...$` 或 `$$...$$` 中。
