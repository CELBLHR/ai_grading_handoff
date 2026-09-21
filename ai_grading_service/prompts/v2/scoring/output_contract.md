# Output JSON

仅输出合法 JSON：

{
  "selected_path_id": "输入 scoring_paths 中选中路径的原始 id；无路径或无法匹配时为 null",
  "suggested_score": 0,
  "max_score": 0,
  "score_points": [
    {
      "point_id": "必须等于当前选中路径 points 或旧版 scoring_points 中某一项的原始 id",
      "score": 0,
      "rationale": "说明该原始评分点是否命中；不得给部分分"
    }
  ],
  "deductions": [],
  "summary": "",
  "mathematical_review": {
    "method_validity": "valid | partially_valid | invalid | unclear | missing",
    "logic_status": "complete | mostly_complete | partial | broken | missing | unclear",
    "final_answer_equivalence": "equivalent | not_equivalent | possibly_equivalent_due_to_ocr | missing | unclear"
  },
  "evidence_usage": {
    "used_evidence_risks": [],
    "uncertain_but_scored_conservatively": []
  },
  "final_answer_status": "correct | partially_correct | incorrect | missing | unclear"
}

# Consistency Check

- `suggested_score` 等于各 score point 得分之和。
- `selected_path_id` 必须始终存在。
- 若输入 `question.rubric.scoring_paths` 非空，`selected_path_id` 必须等于其中一条路径的原始 `id`，并且 `score_points` 必须完整覆盖该路径 `points`；不得使用其他路径的评分点。
- 无法可靠匹配任何路径时，`selected_path_id` 必须为 `null`，`suggested_score` 必须为 0，`score_points` 必须为 `[]`。
- 仅当 `question.rubric.scoring_paths` 为空且 `question.rubric.scoring_points` 非空时，按旧版平铺评分点评分，`selected_path_id` 为 `null`。
- 每个 `score_points[].score` 只能等于 0 或当前生效评分点的满分。
- 不允许部分分；如果评分点满分为 4，则该点只能输出 0 或 4。
- 不得输出输入 rubric 中不存在的 `selected_path_id` 或 `point_id`，不得使用 `rubric_0`、`rubric_1a`、`point_1_sub` 等新增或派生 id。
- 不得把一个输入评分点拆成多个输出评分点；评分点内部只做命中或不命中判断。
- 不输出 `review_advice`。
- 不输出 rubric gap。

# Formula Field Check

- 输出前检查 `summary`、`score_points[].rationale`、`deductions`、`evidence_usage.used_evidence_risks`、`evidence_usage.uncertain_but_scored_conservatively`。
- 上述字段中凡是数学表达式都必须放在 `$...$` 或 `$$...$$` 内，并且表达式本身必须是可渲染 LaTeX。
- 不得在这些字段中保留不可渲染的 OCR 噪声片段；无法可靠转写时，使用中文说明“该公式片段无法可靠转写”。
