from __future__ import annotations

import json
import sys
from pathlib import Path


HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    # 直接运行 Demo 时加入交付包根目录，避免依赖调用方预先配置 PYTHONPATH。
    sys.path.insert(0, str(HANDOFF_ROOT))

from ai_grading_service import grade_homework_answer


def main() -> None:
    """
    功能：读取单题作业 JSON 并输出不含分数的作业评价结果。

    参数：
        无；可通过第一个命令行参数指定输入 JSON 路径。

    返回：
        None
    """
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("sample_homework_input.json")
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    result = grade_homework_answer(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
