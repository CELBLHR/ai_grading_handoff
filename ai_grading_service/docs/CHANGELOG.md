# AI 批改方法包版本记录

## v2.6

- 新增 `generate_question_from_image()`，支持从图片识别题干、标题、建议题型、分数、标准答案和评分标注。
- 新增 `generate_question_from_text()`，支持从题干生成标题、建议题型、标准答案和考题评分路径。
- 图片方法不接收外部分数；考题优先使用截图分数，识别失败时默认 10 分，作业不使用分数。
- 图片内容区分 `stem_only`、`solution_unannotated` 和 `solution_annotated` 三种场景。
- 完整标注图片只执行视觉识别，不调用生成模型改写老师内容。
- 新增答案、评分路径和分数来源字段，并返回实际模型调用次数。
- 题型由生成接口给出 AI 建议值，老师确认保存后仍作为现有批改接口的正式输入。
- 正式批改入口继续要求题目配置提供题型，避免缺失后静默使用 `unknown` 通用规则。
- 保留 v2.5 全部考试批改、作业评价、方法聚合和拆分调用行为。

## v2.5

- 新增 `grade_homework_answer()`，复用视觉、证据风险和方法分析链路，不执行考试评分。
- 新增 `grade_homework_answers()` 顺序批量辅助入口。
- 作业评价输入允许省略 `question.max_score` 和 `question.rubric`。
- 新增 `homework_feedback`，包含作答状态、优点、具体问题和订正建议。
- 作业评价禁止输出分数、评分点、扣分项或评分路径。
- 新增计算、证明、填空、讨论、混合和未知题型的作业评价规则。
- 作业评价检测到明显裸公式时只纠正一次；仍不合格或纠正时改变评价结构则返回失败。
- 新增 `HOMEWORK_REVIEW_MODE=off|shadow|visible`；默认 `off`，不调用独立复核模型。
- `extract_visual_answer()` 和 `analyze_answer_method()` 支持无满分的作业输入。
- 保留 v2.4 的考试批改、多路径评分、方法聚合和聚合 LaTeX 修复行为。

## v2.4

- 补齐方法聚合输出的 LaTeX 公式格式约束，覆盖聚类、候选、排除项和评分路径草案。
- 方法聚合首次返回明显裸公式时自动要求模型纠正一次；纠正后仍不合格则返回失败，不输出不可渲染报告。
- 保留 `grade_answer()` 一步式批改入口。
- 新增 `extract_visual_answer()`，仅执行视觉提取。
- 新增 `analyze_answer_method()`，执行证据风险与方法分析，不打分。
- `discover_method_clusters()` 支持 `method_pool`，并兼容旧字段 `summary_pool`。
- 方法聚合结果原样回传 `current_rubric`。
- 支持将视觉、证据风险和方法分析结果回填给 `grade_answer()`，跳过重复模型调用。
- 正式支持 `rubric.scoring_paths` 多路径评分，返回 `selected_path_id`。
- 多路径评分只允许使用选中路径的评分点，禁止跨路径累计。
- 无法匹配评分路径时强制进入人工复核。
- 视觉、方法分析、完整批改和方法聚合均支持协作式取消。
- 聚合生成的 `scoring_path_draft` 包含路径 ID 和评分点 ID。

## v2.3

- 在严格评分点对齐基础上增加二值评分。
- 每个评分点只能得到 0 分或该点评分满分，不允许部分分。
- 校验 `suggested_score` 等于各评分点得分之和。
- 要求说明性出参中的数学公式使用可渲染 LaTeX。
- scoring 阶段的 `prompt_version` 为 `v2.3`。

## v2.2

- 输出评分点必须严格对齐输入 `question.rubric.scoring_points`。
- 禁止模型新增、改名、合并或拆分评分点。
- scoring 阶段的 `prompt_version` 为 `v2.2`。

## v2.0

- 支持通过 `student_answer.image_url` 传入 HTTP/HTTPS 图片。
- 增加模型原始输出保存开关。
- 增加 `cancel_event` 和 `cancel_checker` 协作式取消。
- 保留允许模型根据 rubric 自行拆分评分点的历史行为。

## 共同修复

- `review_decision.review_focus` 会规范化为 JSON 数组。
- 模型返回字符串、对象或 `null` 时不会再因字段类型导致整题失败。
