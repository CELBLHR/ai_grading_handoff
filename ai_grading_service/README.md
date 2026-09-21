# AI 批改方法包 v2.6

本包提供可由 Django 或普通 Python 服务直接调用的批改函数，不提供 HTTP 服务，也不负责数据库写入、批量分组或任务队列。

考试批改继续严格使用输入评分点、每点评分只能为 0 或满分并支持多条评分路径。v2.5 的无分数作业评价入口保持不变；v2.6 新增图片和题干两种题目配置生成入口。所有说明性公式继续使用可渲染 LaTeX。

## 对外函数

| 函数 | 主要输入 | 主要输出 | 是否评分 |
| --- | --- | --- | --- |
| `extract_visual_answer` | 题目、作答图片或文本 | `visual_ocr` | 否 |
| `analyze_answer_method` | 视觉结果 | `method_summary` | 否 |
| `discover_method_clusters` | 同题多份方法摘要 | 方法聚类与路径候选 | 否 |
| `grade_answer` | 原始单题输入或已有阶段结果 | 完整批改结果 | 是 |
| `grade_answers` | 多份单题输入 | 批改结果数组 | 是 |
| `grade_homework_answer` | 原始单题输入或已有阶段结果 | 作业评价与订正建议 | 否 |
| `grade_homework_answers` | 多份作业单题输入 | 作业评价结果数组 | 否 |
| `generate_question_from_image` | 题目图片、作业/考题类型 | 题干、标题、建议题型、答案及可选评分路径 | 否 |
| `generate_question_from_text` | 题干、作业/考题类型、考题分数 | 标题、建议题型、答案及可选评分路径 | 否 |

调用方负责并发、重试、取消状态维护、结果入库和前端展示。`grade_answers` 只提供顺序批量辅助，生产并发建议由 Django、Celery 或任务队列控制。

## 快速开始

进入交付包根目录后运行：

```powershell
python ai_grading_service/demo_grade_answer.py ai_grading_service/sample_input.json
```

默认可以使用 Mock 完成本地联调。真实模型配置参考 `.env.example`，不要将真实 API Key 写入交付包。

题目配置生成 Demo：

```powershell
python ai_grading_service/demo_generate_question.py ai_grading_service/sample_question_text_input.json
python ai_grading_service/demo_generate_question.py ai_grading_service/sample_question_image_input.json
```

## 题目配置生成

题干方法：

```python
from ai_grading_service import generate_question_from_text

result = generate_question_from_text(
    {
        "grading_mode": "exam",
        "question_content": "计算 $1+1$。",
        "score": 10,
    }
)
```

图片方法：

```python
from ai_grading_service import generate_question_from_image

result = generate_question_from_image(
    {
        "grading_mode": "exam",
        "image_path": "question.jpg",
    }
)
```

图片方法不接收 `score`。考题分数优先从图片识别，未识别到时使用默认 10 分；作业不使用分数。图片中只有题干或缺少评分标注时才调用第二次模型补齐内容，完整标注图片只执行一次识别。

两个生成入口返回的 `question_type` 是 AI 建议值，需由老师确认后保存。正式批改入口中的 `question.question_type` 继续必传，并用于选择对应题型提示词。

## 一步式批改

```python
from ai_grading_service import grade_answer

result = grade_answer(payload)
```

基础输入参考 `sample_input.json`：

```json
{
  "project_id": "project_001",
  "answer_item_id": "answer_001",
  "question": {
    "question_id": "question_001",
    "question_no": "1",
    "question_type": "calculation",
    "stem": "计算 1 + 1。",
    "standard_answer": "1 + 1 = 2",
    "max_score": 2,
    "rubric": {
      "scoring_points": [
        {"id": "p1", "description": "过程正确", "score": 1},
        {"id": "p2", "description": "答案正确", "score": 1}
      ]
    }
  },
  "student_answer": {
    "text": "1 + 1 = 2"
  }
}
```

`student_answer` 支持 `image_path`、`image_url`、`ocr_text` 或 `text`。存在图片时执行视觉模型，否则使用传入文本构造视觉阶段结果。

## 作业评价

```python
from ai_grading_service import grade_homework_answer

result = grade_homework_answer(payload)
```

输入结构与 `grade_answer()` 基本一致，但 `question.max_score` 和 `question.rubric` 均可省略。基础样例见 `sample_homework_input.json`，主要返回如下：

```text
summary # 面向当前学生的总体评价
homework_feedback.answer_status # correct/partially_correct/incorrect/incomplete/blank/unclear
homework_feedback.strengths # 有证据支持的正确部分
homework_feedback.issues # 问题位置、证据、原因和订正方向
homework_feedback.improvement_suggestions # 后续订正建议
```

作业评价不输出分数：`suggested_score`、`final_score` 和 `selected_path_id` 固定为 `null`，`score_points` 和 `deductions` 固定为空数组。

独立作业复核由环境变量控制：

```text
HOMEWORK_REVIEW_MODE=off     # 默认，不调用复核模型
HOMEWORK_REVIEW_MODE=shadow  # 调用复核模型，但不覆盖顶层结论
HOMEWORK_REVIEW_MODE=visible # 调用复核模型并公开复核结论
```

`off` 模式共执行 `visual_ocr -> evidence_risk -> method_analysis -> homework_review` 四个阶段。完整示例：

```powershell
python ai_grading_service/demo_grade_homework_answer.py ai_grading_service/sample_homework_input.json
```

## 拆分式流程

拆分结果可以直接传给下一段，不需要重新拼接原始题目：

```python
from ai_grading_service import (
    analyze_answer_method,
    discover_method_clusters,
    extract_visual_answer,
    grade_answer,
)

# 单份作答：视觉提取 -> 方法分析。
visual_result = extract_visual_answer(payload)
method_result = analyze_answer_method(visual_result)

# 对接方按照 question_id 收集同一道题的方法结果。
cluster_result = discover_method_clusters({
    "project_id": "project_001",
    "question": payload["question"],
    "current_rubric": payload["question"]["rubric"],
    "method_pool": [method_result],
    "threshold": {"min_cluster_count": 2},
})

# 也可以复用前三个阶段的结果继续正式批改。
grading_result = grade_answer(method_result)
```

完整可运行示例见 `demo_split_pipeline.py` 和 `demo_method_clusters.py`。

### 从一组图片开始聚合

复制 `sample_image_group_cluster_input.json`，将 `answers[]` 中的 `image_path` 或 `image_url` 替换为同一道题的真实作答图片，然后运行：

```powershell
python ai_grading_service/demo_group_images_to_clusters.py ai_grading_service/sample_image_group_cluster_input.json
```

该 Demo 对每张图片依次执行视觉提取和方法分析，再把成功结果统一送入方法聚合。返回中的 `failed_answers` 会列出未能进入聚合的图片及失败阶段。输入图片应当是同一道题的作答区域，而不是混合多个题目的整张试卷。

## 多路径批改

多路径输入参考 `sample_multi_path_input.json`：

```json
{
  "rubric": {
    "scoring_paths": [
      {
        "id": "path_1",
        "name": "方法一",
        "applicability": "学生使用方法一时采用",
        "points": [
          {"id": "path_1_point_1", "description": "关键步骤", "score": 3}
        ]
      },
      {
        "id": "path_2",
        "name": "方法二",
        "applicability": "学生使用方法二时采用",
        "points": [
          {"id": "path_2_point_1", "description": "关键步骤", "score": 3}
        ]
      }
    ]
  }
}
```

所有路径一起传给评分模型，模型返回 `selected_path_id`。程序只允许使用选中路径的评分点，禁止跨路径混用或累计。无法可靠匹配路径时返回 `selected_path_id = null`，并强制进入人工复核。

## 方法聚合

`discover_method_clusters()` 的 `method_pool` 必须属于同一道题。它可以接收精简的 `method_summary` 对象，也可以直接接收 `analyze_answer_method()` 的完整返回对象。

主要返回字段：

```text
current_rubric # 输入评分规则原样回传，不经过模型
method_clusters # 高频方法聚类
gap_candidates # 当前 rubric 未覆盖的新路径候选
excluded_candidates # 已覆盖、错误或无法确认的候选
```

聚合只生成供老师审核的 `scoring_path_draft`，不会自动修改正式 rubric。

## 取消调用

所有主要入口都支持 `cancel_event` 或 `cancel_checker`。它们是 Python 函数参数，不是 `payload` JSON 字段：

```python
result = grade_answer(payload, cancel_event=event)
result = grade_homework_answer(payload, cancel_event=event)
result = extract_visual_answer(payload, cancel_checker=cancel_checker)
result = analyze_answer_method(visual_result, cancel_checker=cancel_checker)
result = discover_method_clusters(cluster_payload, cancel_checker=cancel_checker)
```

取消是协作式机制，无法中断已经发出的模型 HTTP 请求。请求返回后检测到取消时，模型结果会被丢弃，并返回 `status = "cancelled"`。

## 运行示例

```powershell
# 一步式批改
python ai_grading_service/demo_grade_answer.py ai_grading_service/sample_input.json

# 作业评价
python ai_grading_service/demo_grade_homework_answer.py ai_grading_service/sample_homework_input.json

# 多路径批改
python ai_grading_service/demo_grade_answer.py ai_grading_service/sample_multi_path_input.json

# 方法聚合
python ai_grading_service/demo_method_clusters.py ai_grading_service/sample_method_cluster_input.json

# 从同题图片组开始进行方法聚合（先替换样例中的图片地址）
python ai_grading_service/demo_group_images_to_clusters.py ai_grading_service/sample_image_group_cluster_input.json

# 完整拆分流程
python ai_grading_service/demo_split_pipeline.py ai_grading_service/sample_input.json
```

## 详细文档

- 完整函数签名、字段和返回结构：[`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)
- 各版本变化：[`docs/CHANGELOG.md`](docs/CHANGELOG.md)
- 环境变量模板：`.env.example`
- 成功与失败返回样例：`sample_success_output.json`、`sample_failed_output.json`
