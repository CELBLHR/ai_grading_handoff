from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    # 直接运行脚本时加入交付包根目录，确保可以按包名导入。
    sys.path.insert(0, str(HANDOFF_ROOT))

from ai_grading_service import (  # noqa: E402
    analyze_answer_method,
    discover_method_clusters,
    extract_visual_answer,
)


def resolve_student_answer(answer: dict[str, Any], manifest_dir: Path) -> dict[str, Any]:
    """
    功能：整理单张图片来源，并把相对路径转换为相对于清单文件的绝对路径。

    参数：
        answer (dict[str, Any]): answers 数组中的单份作答配置。
        manifest_dir (Path): 图片组清单文件所在目录。

    返回：
        dict[str, Any]: 可直接传给视觉函数的 student_answer。
    """
    student_answer = answer.get("student_answer")
    if isinstance(student_answer, dict):
        normalized_answer = dict(student_answer)
    else:
        # 支持把 image_path 和 image_url 直接写在 answers[] 中，降低清单嵌套层级。
        normalized_answer = {
            "image_path": answer.get("image_path", ""),
            "image_url": answer.get("image_url", ""),
        }

    image_path = str(normalized_answer.get("image_path") or "").strip()
    image_url = str(normalized_answer.get("image_url") or "").strip()
    if image_path:
        resolved_path = Path(image_path)
        if not resolved_path.is_absolute():
            resolved_path = (manifest_dir / resolved_path).resolve()
        if not resolved_path.is_file():
            raise ValueError(f"作答图片不存在：{resolved_path}")
        normalized_answer["image_path"] = str(resolved_path)
    elif not image_url:
        raise ValueError("每份作答必须提供 image_path 或 image_url")

    return normalized_answer


def build_single_answer_payload(
    manifest: dict[str, Any],
    answer: dict[str, Any],
    manifest_dir: Path,
) -> dict[str, Any]:
    """
    功能：将图片组公共题目信息和单份作答信息合并为视觉函数入参。

    参数：
        manifest (dict[str, Any]): 图片组清单。
        answer (dict[str, Any]): 当前单份作答配置。
        manifest_dir (Path): 清单文件所在目录。

    返回：
        dict[str, Any]: extract_visual_answer 可消费的单题入参。
    """
    answer_item_id = str(answer.get("answer_item_id") or "").strip()
    if not answer_item_id:
        raise ValueError("answers[].answer_item_id 不能为空")
    question = manifest.get("question")
    if not isinstance(question, dict):
        raise ValueError("question 必须是 JSON 对象")

    return {
        "project_id": str(manifest.get("project_id") or "external_project"),
        "task_id": str(manifest.get("task_id") or "method_discovery"),
        "pipeline_run_id": str(manifest.get("pipeline_run_id") or manifest.get("run_id") or "image_group_run"),
        "answer_item_id": answer_item_id,
        "student": answer.get("student") if isinstance(answer.get("student"), dict) else {},
        "question": question,
        "student_answer": resolve_student_answer(answer, manifest_dir),
    }


def process_image_group(manifest: dict[str, Any], manifest_dir: Path) -> dict[str, Any]:
    """
    功能：依次处理同一道题的多张作答图片，并聚合成功识别的方法摘要。

    参数：
        manifest (dict[str, Any]): 包含公共题目、评分规则和 answers 图片数组的清单。
        manifest_dir (Path): 清单文件所在目录，用于解析相对图片路径。

    返回：
        dict[str, Any]: 处理统计、方法摘要池、失败图片和最终聚合结果。
    """
    answers = manifest.get("answers")
    if not isinstance(answers, list) or not answers:
        raise ValueError("answers 必须是非空数组")

    method_pool: list[dict[str, Any]] = []
    failed_answers: list[dict[str, Any]] = []
    for index, answer in enumerate(answers):
        if not isinstance(answer, dict):
            failed_answers.append(
                {
                    "answer_item_id": "",
                    "stage": "input",
                    "error": {"code": "ValueError", "message": f"answers[{index}] 必须是 JSON 对象"},
                }
            )
            continue

        answer_item_id = str(answer.get("answer_item_id") or "")
        try:
            single_payload = build_single_answer_payload(manifest, answer, manifest_dir)
        except Exception as exc:  # noqa: BLE001
            failed_answers.append(
                {
                    "answer_item_id": answer_item_id,
                    "stage": "input",
                    "error": {"code": exc.__class__.__name__, "message": str(exc)},
                }
            )
            continue

        visual_result = extract_visual_answer(single_payload)
        if visual_result.get("status") != "success":
            failed_answers.append(
                {
                    "answer_item_id": answer_item_id,
                    "stage": "visual_ocr",
                    "error": visual_result.get("error"),
                }
            )
            continue

        method_result = analyze_answer_method(visual_result)
        if method_result.get("status") != "success":
            failed_answers.append(
                {
                    "answer_item_id": answer_item_id,
                    "stage": "method_analysis",
                    "error": method_result.get("error"),
                }
            )
            continue

        # 聚合函数支持完整方法结果，这里保留完整对象以便对接方审计单份处理过程。
        method_pool.append(method_result)

    cluster_result: dict[str, Any] | None = None
    if method_pool:
        question = manifest["question"]
        cluster_result = discover_method_clusters(
            {
                "project_id": str(manifest.get("project_id") or "external_project"),
                "run_id": str(manifest.get("run_id") or "image_group_cluster_run"),
                "question": question,
                "current_rubric": manifest.get("current_rubric", question.get("rubric", {})),
                "method_pool": method_pool,
                "threshold": manifest.get("threshold", {}),
            }
        )

    cluster_succeeded = cluster_result is not None and cluster_result.get("status") == "success"
    if not method_pool or not cluster_succeeded:
        overall_status = "failed"
    elif failed_answers:
        overall_status = "partial"
    else:
        overall_status = "success"

    return {
        "status": overall_status,
        "total_images": len(answers),
        "method_success_count": len(method_pool),
        "failed_count": len(failed_answers),
        "method_pool": [
            {
                "answer_item_id": item.get("answer_item_id", ""),
                "method_summary": item.get("method_summary", ""),
                "method_confidence": item.get("method_confidence", "low"),
            }
            for item in method_pool
        ],
        "failed_answers": failed_answers,
        "cluster_result": cluster_result,
    }


def main() -> int:
    """
    功能：读取图片组清单并输出方法聚合结果。

    参数：
        无。清单路径通过第一个命令行参数传入。

    返回：
        int: 执行成功返回 0，清单级错误返回 1。
    """
    manifest_path = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path(__file__).with_name("sample_image_group_cluster_input.json")
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("图片组清单必须是 JSON 对象")
        result = process_image_group(manifest, manifest_path.parent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": {"code": exc.__class__.__name__, "message": str(exc)},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
