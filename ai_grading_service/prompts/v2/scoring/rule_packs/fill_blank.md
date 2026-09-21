# Fill Blank Rule Pack

适用于单空或多空填空题。填空题默认考查每个空对应答案的正确性，不要求学生展示计算或推导过程。

- 必须按当前 rubric 中的给分点逐空评分。每个空只对应自己的给分点，某一空错误或缺失不能取消其他空已经获得的分数。
- 必须结合题干、空位顺序、`description`、`reference` 和 `grading_rule` 确认答案归属；不能把后一个空的答案补到前一个空，也不能擅自交换答案顺序。
- 数学表达与参考答案数学等价时应判为正确；文字答案语义等价，或属于 rubric 明确允许的备选答案时，也应判为正确。
- 单位、有效数字、符号形式、书写格式和答案顺序只有在 rubric 明确要求时才作为扣分依据。
- 题目或 rubric 未要求过程时，不得因为学生没有计算步骤、解释或推导而扣分；明确设置过程给分点时，才按该给分点看到的有效证据评分。
- 清楚未填写的空按缺失处理；OCR、涂改、答案归属或关键符号不确定时，只按可确认的证据评分，并在 `evidence_usage` 中记录不确定性。
- 所有空均正确时，`final_answer_status` 输出 `correct`；至少一空正确但并非全部正确时输出 `partially_correct`；所有已填写答案均错误时输出 `incorrect`；所有空均未填写时输出 `missing`；证据不足以区分上述状态时输出 `unclear`。
- `mathematical_review.final_answer_equivalence` 表示整道填空题的综合等价状态：全部等价为 `equivalent`，存在明确不等价答案为 `not_equivalent`，仅因 OCR 无法确认时使用 `possibly_equivalent_due_to_ocr` 或 `unclear`。
