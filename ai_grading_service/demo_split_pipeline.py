from __future__ import annotations

import json
import sys
from pathlib import Path

HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    # 直接运行脚本时加入交付包根目录，确保可以按包名导入。
    sys.path.insert(0, str(HANDOFF_ROOT))

from ai_grading_service import (
    analyze_answer_method,
    discover_method_clusters,
    extract_visual_answer,
    grade_answer,
)


def main() -> int:
    """依次演示视觉提取、方法分析、方法聚类和复用结果批改。"""
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("sample_input.json")
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    visual_result = extract_visual_answer(payload)

    method_result = analyze_answer_method(visual_result)

    cluster_payload = {
        "project_id": payload.get("project_id"),
        "run_id": "split_pipeline_cluster_demo",
        "question": payload["question"],
        "current_rubric": payload["question"].get("rubric", {}),
        "method_pool": [method_result],
        "threshold": {"min_cluster_count": 1},
    }
    cluster_result = discover_method_clusters(cluster_payload)

    grading_result = grade_answer(method_result)

    print(
        json.dumps(
            {
                "visual": visual_result,
                "method": method_result,
                "clusters": cluster_result,
                "grading": grading_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
