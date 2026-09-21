# AI 批改方法包 API 参考 v2.6

本文档记录完整函数签名、输入输出字段和运行规则。首次接入请先阅读根目录 `README.md`。

## 公共类型

### CancelChecker

```python
CancelChecker = Callable[[], bool]
```

返回 `True` 表示调用方已经请求取消。

### 单题基础输入

```text
project_id: string, optional # 外部项目 ID
task_id: string, optional # 外部任务 ID
pipeline_run_id: string, optional # 外部运行批次 ID
answer_item_id: string, required # 单份学生作答唯一 ID
student: object, optional # 学生信息
student.student_id: string, optional
student.student_no: string, optional
student.name: string, optional
question: object, required # 题目信息
question.question_id: string, optional
question.question_no: string, required
question.question_type: string, required # calculation、proof、discussion、mixed、fill_blank
question.stem: string, optional
question.standard_answer: string, optional
question.max_score: number, conditionally required # grade_answer 必填；作业评价可省略
question.rubric: object, optional # 作业评价不使用评分规则
student_answer: object, conditionally required # 未提供 visual_ocr 时必须提供
student_answer.image_path: string, optional
student_answer.image_url: string, optional
student_answer.ocr_text: string, optional
student_answer.text: string, optional
```

未传入已有 `visual_ocr` 时，`student_answer` 的四种作答来源至少提供一种，优先级为：

```text
image_path > image_url > ocr_text > text
```

## extract_visual_answer

```python
extract_visual_answer(payload, cancel_event=None, cancel_checker=None)
```

仅执行视觉提取，不分析方法、不评分。

成功返回：

```text
status: string # success
project_id: string
answer_item_id: string
question_id: string
question: object # 规范化题目信息
student: object
visual_ocr: object
stage_outputs.visual_ocr: object
stage_results: array
error: null
```

返回结果可直接作为 `analyze_answer_method()` 的输入。

## analyze_answer_method

```python
analyze_answer_method(payload, cancel_event=None, cancel_checker=None)
```

执行视觉结果复用或视觉提取、证据风险分析和方法分析，不执行评分。

成功返回：

```text
status: string # success
project_id: string
answer_item_id: string
question_id: string
question: object
student: object
method_summary: string
method_confidence: string # high、medium、low
method_evidence_blocks: array
method_analysis: object
stage_outputs: object # visual_ocr、evidence_risk、method_analysis
stage_results: array
error: null
```

返回结果可直接作为 `grade_answer()` 或 `grade_homework_answer()` 的输入，也可以直接放入方法聚合的 `method_pool`。

## grade_answer

```python
grade_answer(payload, cancel_event=None, cancel_checker=None)
```

完整流程为：

```text
visual_ocr -> evidence_risk -> method_analysis -> scoring -> review_decision
```

可以在顶层或 `stage_outputs` 中传入已有的 `visual_ocr`、`evidence_risk` 和 `method_analysis`。通过校验后，相应阶段不会重复调用模型。

复用阶段在 `stage_results` 中标记：

```text
prompt_version: reused
source_type: supplied_stage_output
```

成功返回：

```text
answer_item_id: string
status: string # success
suggested_score: number
max_score: number
final_score: number|null # auto_accept 时等于 suggested_score
question_type: string
selected_path_id: string|null
summary: string
score_points: array
score_points[].point_id: string
score_points[].score: number
score_points[].rationale: string
deductions: array
confidence_level: string # high、medium、low
review_advice: string # auto_accept、sample_review、required_review、second_review
risk_tags: array
review_focus: array
evidence_safe: boolean
stage_outputs: object
stage_results: array
error: null
```

### 单路径评分规则

旧版输入使用：

```text
question.rubric.scoring_points: array
question.rubric.scoring_points[].id: string
question.rubric.scoring_points[].description: string
question.rubric.scoring_points[].score: number
```

输出必须覆盖全部输入评分点，不得新增、改名、合并或拆分。每点评分只能是 0 或该点满分。

### 多路径评分规则

参考 `sample_multi_path_input.json`。

```text
question.rubric.scoring_paths: array
question.rubric.scoring_paths[].id: string, required
question.rubric.scoring_paths[].name: string, optional
question.rubric.scoring_paths[].reference_solution: string, optional
question.rubric.scoring_paths[].applicability: string, optional
question.rubric.scoring_paths[].points: array, required
question.rubric.scoring_paths[].points[].id: string, required
question.rubric.scoring_paths[].points[].description: string
question.rubric.scoring_paths[].points[].score: number
```

所有路径会随题目一起传给评分模型。模型返回 `selected_path_id` 后，代码只允许使用该路径的全部评分点，并继续执行评分点 ID、二值分数和总分校验。

```text
scoring_paths 非空 # 多路径优先，忽略平铺 scoring_points
selected_path_id 命中 # 只按选中路径评分
selected_path_id 为 null # 只允许 0 分和空 score_points，并强制 required_review
scoring_paths 为空 # 使用旧版 scoring_points
```

路径 ID 必须唯一；每条路径内的评分点 ID 也必须唯一。

## grade_homework_answer

```python
grade_homework_answer(payload, cancel_event=None, cancel_checker=None)
```

执行不含分数的单题作业评价。输入结构与 `grade_answer()` 基本一致，但允许省略 `question.max_score` 和 `question.rubric`。

默认流程：

```text
visual_ocr -> evidence_risk -> method_analysis -> homework_review
```

成功返回：

```text
answer_item_id: string
status: string # success
grading_mode: string # homework
suggested_score: null
max_score: number|null # 输入提供时原样规范化，否则为 null
final_score: null
question_type: string
selected_path_id: null
summary: string # 对当前学生本题作答的总体评价
score_points: array # 固定为空
deductions: array # 固定为空
homework_feedback: object
homework_feedback.answer_status: string # correct、partially_correct、incorrect、incomplete、blank、unclear
homework_feedback.summary: string
homework_feedback.strengths: array
homework_feedback.issues: array
homework_feedback.issues[].type: string
homework_feedback.issues[].location: string
homework_feedback.issues[].evidence: string
homework_feedback.issues[].explanation: string
homework_feedback.issues[].correction_guidance: string
homework_feedback.improvement_suggestions: array
homework_feedback.evidence_usage: object
confidence_level: string
review_advice: string
risk_tags: array
review_focus: array
evidence_safe: boolean
homework_review_mode: string # off、shadow、visible
homework_review_status: string # not_run、shadow、visible
stage_outputs: object
stage_results: array
error: null
```

可以复用 `visual_ocr`、`evidence_risk`、`method_analysis` 和 `homework_review`。作业评价契约禁止模型输出任何分数、评分点、扣分项或评分路径。

作业评价的 `summary`、优点、问题说明、卷面证据、订正建议和复核说明中的公式必须使用可渲染 LaTeX。真实模型首次返回明显裸公式时会进行一次纯格式纠正；第二次仍不合格或纠正时改变 `answer_status`、问题数量、问题类型和可信度结构，则本题返回失败。

### 作业复核模式

```text
off # 默认；不调用 homework_review_decision，stage_outputs 中对应值为 null
shadow # 调用独立复核并保留阶段输出，但顶层可信度仍使用 homework_review 的初步结论
visible # 调用独立复核，并用其结果覆盖顶层 confidence_level 等可信度字段
```

复核模式不会修改 `homework_feedback` 正文。

## grade_homework_answers

```python
grade_homework_answers(payloads, cancel_event=None, cancel_checker=None)
```

顺序调用多个 `grade_homework_answer()`，返回顺序与输入一致。生产并发仍由调用方负责。

## grade_answers

```python
grade_answers(payloads, cancel_event=None, cancel_checker=None)
```

顺序调用多个 `grade_answer()`，返回与输入顺序一致的结果数组。生产环境的并发、任务队列和失败重试应由调用方负责。

## discover_method_clusters

```python
discover_method_clusters(payload, cancel_event=None, cancel_checker=None)
```

输入同一道题的多份方法摘要，生成方法聚类和当前 rubric 未覆盖的评分路径候选。该函数不评分，也不负责跨题分组。

输入：

```text
project_id: string, optional
run_id: string, optional
question: object, required
current_rubric: object|string, optional # 不传时使用 question.rubric
method_pool: array, required # 兼容旧名称 summary_pool
method_pool[].answer_item_id: string, required
method_pool[].student_no: string, optional
method_pool[].suggested_score: number, optional
method_pool[].method_summary: string, required
method_pool[].method_analysis: object, optional
method_pool[].stage_outputs.method_analysis: object, optional
threshold: object, optional
threshold.min_cluster_count: number, optional
```

`method_pool` 可以直接接收 `analyze_answer_method()` 的完整返回对象，函数会自动提取 `method_summary`。

成功返回：

```text
status: string # success
project_id: string
question_id: string
current_rubric: object|string # 输入内容原样回传，不经过模型
method_clusters: array
method_clusters[].cluster_name: string
method_clusters[].count: number
method_clusters[].representative_answer_item_ids: array
method_clusters[].representative_summaries: array
method_clusters[].reason: string
gap_candidates: array
gap_candidates[].candidate_type: string
gap_candidates[].method: string
gap_candidates[].count: number
gap_candidates[].coverage_status: string
gap_candidates[].representative_answer_item_ids: array
gap_candidates[].representative_summaries: array
gap_candidates[].reason: string
gap_candidates[].scoring_path_draft: object
gap_candidates[].scoring_path_draft.id: string
gap_candidates[].scoring_path_draft.points[].id: string
excluded_candidates: array
raw_output: object
model_metadata: object
error: null
```

`scoring_path_draft` 只供老师审核，确认后才能写入正式 rubric。

方法聚合中的以下展示字段如果包含数学表达式，必须使用 `$...$` 或 `$$...$$`：

```text
method_clusters[].cluster_name
method_clusters[].representative_summaries[]
method_clusters[].reason
gap_candidates[].method
gap_candidates[].representative_summaries[]
gap_candidates[].reason
gap_candidates[].scoring_path_draft 中的说明性字段
excluded_candidates[].method
excluded_candidates[].representative_summaries[]
excluded_candidates[].reason
```

真实模型首次返回明显裸公式、未包裹 LaTeX 命令或未闭合定界符时，服务会要求模型只纠正公式格式并重试一次。第二次仍不符合要求时，`discover_method_clusters()` 返回 `status = "failed"`，错误码为 `ValueError`，不会把该聚合结果作为成功报告交付。

### 图片组聚合 Demo

`demo_group_images_to_clusters.py` 演示从同一道题的一组作答图片开始调用：

```text
answers[].image_path/image_url
-> extract_visual_answer
-> analyze_answer_method
-> method_pool
-> discover_method_clusters
```

清单格式参考 `sample_image_group_cluster_input.json`。公共 `question` 和 `current_rubric` 只需提供一次，每张图片在 `answers[]` 中提供独立的 `answer_item_id`、`student` 和图片地址。相对 `image_path` 按清单文件所在目录解析。

Demo 返回：

```text
status: success|partial|failed
total_images: number
method_success_count: number
failed_count: number
method_pool: array # 成功提取的方法摘要
failed_answers: array # 失败图片、失败阶段和错误信息
cluster_result: object|null # discover_method_clusters 的原始结果
```

## 取消行为

`cancel_event` 和 `cancel_checker` 是 Python 函数参数，不属于 `payload` JSON。

```text
grade_answer # 每个阶段开始前检查
grade_answers # 参数透传给每个 grade_answer
extract_visual_answer # 视觉调用前检查
analyze_answer_method # 开始前和各模型阶段之间检查
discover_method_clusters # 聚合调用前和返回后检查
```

取消是协作式机制，不能中断已经发出的 HTTP 请求。请求返回后若检测到取消，函数会丢弃模型结果。

取消结果统一包含：

```text
status: cancelled
cancelled: true
error.code: CANCELLED
```

方法聚合取消时会保留 `current_rubric`，并将聚类与候选数组清空。其他拆分函数会在 `stage_outputs` 和 `stage_results` 中保留取消前已经完成的阶段。

## 失败行为

公开函数不会向调用方直接抛出普通运行异常，而是返回：

```text
status: failed
error.code: 异常类型
error.message: 错误说明
```

完整批改失败时还会返回 `review_advice = required_review` 和 `risk_tags = ["ai_grading_failed"]`。

## LaTeX 输出要求

面向教师或前端展示的文本中，数学表达式使用 `$...$` 或 `$$...$$`。无法可靠识别时使用中文说明或 `$\\text{[unrecognizable]}$`，不得保留不可渲染的 OCR 噪声。

重点字段包括：

```text
summary
score_points[].rationale
deductions[]
review_focus[]
stage_outputs.visual_ocr 中的转写文本与公式候选
stage_outputs.evidence_risk 中的风险说明
stage_outputs.method_analysis.method_summary
stage_outputs.scoring 中的说明性字段
stage_outputs.review_decision 中的复核说明
method_clusters[] 中的名称、代表摘要和聚类原因
gap_candidates[] 中的候选说明及 scoring_path_draft 说明性字段
excluded_candidates[] 中的方法、代表摘要和排除原因
```

## generate_question_from_image

```python
generate_question_from_image(payload)
```

输入：

```text
grading_mode: string, required # exam 或 homework
image_path: string, conditionally required
image_url: string, conditionally required
```

`image_path` 和 `image_url` 必须且只能提供一个。该方法禁止传入 `score`。考题从截图识别分值，识别失败时使用默认 10 分；作业分数固定为 `null`。

图片内容场景：

```text
stem_only                 # 只有题干，AI 补齐答案和考题评分路径
solution_unannotated      # 已有作答，提取最终答案，AI 只补齐考题评分路径
solution_annotated        # 已有标准作答和完整评分标注，只识别，不生成
```

## generate_question_from_text

```python
generate_question_from_text(payload)
```

输入：

```text
grading_mode: string, required # exam 或 homework
question_content: string, required
score: number, conditionally required # exam 必填；homework 不传
```

两个题目生成方法成功时使用相同的核心输出：

```text
status: string # success
grading_mode: string
question_title: string
question_content: string
question_type: string # AI 建议题型，需老师确认
question_type_source: string # ai_inferred
question_type_confidence: string # high、medium、low
score: number|null
score_source: string # input、image_extracted、default、not_applicable
content_mode: string
standard_answers: array
solution_text: string # 考题标准解题过程；作业固定为空字符串
is_answer_ai_generated: boolean
answer_source: string # ai_generated 或 image_extracted
scoring_paths: array
is_scoring_paths_ai_generated: boolean|null
scoring_paths_source: string # ai_generated、image_extracted、not_applicable
warnings: array
model_call_count: integer
model_stages: array
```

AI 生成的每条考题评分路径都必须满足评分点合计等于 `score`。图片中老师原始标注的合计不一致时保留原值，并通过 `warnings` 要求人工确认。

## 环境变量

真实 `.env` 不随包交付，配置模板见 `.env.example`。

```text
AI_PIPELINE_PROVIDER: mock|openai_compatible
HOMEWORK_REVIEW_MODE: off|shadow|visible
VISUAL_MODEL_API_KEY: string
VISUAL_MODEL_BASE_URL: string
VISUAL_MODEL_NAME: string
VISUAL_MODEL_TIMEOUT_SECONDS: integer
GRADING_MODEL_API_KEY: string
GRADING_MODEL_BASE_URL: string
GRADING_MODEL_NAME: string
GRADING_MODEL_TIMEOUT_SECONDS: integer
GATE_MODEL_API_KEY: string, optional
GATE_MODEL_BASE_URL: string, optional
GATE_MODEL_NAME: string, optional
AI_GRADING_SAVE_RAW_OUTPUT: true|false
```

各阶段模型配置的优先级和兼容别名以 `.env.example` 为准。
