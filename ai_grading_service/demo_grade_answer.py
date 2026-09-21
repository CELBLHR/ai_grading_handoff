from __future__ import annotations

import json
import sys
from pathlib import Path

HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    # 直接运行本脚本时，手动加入 handoff 目录，确保可以按包名导入交付模块。
    sys.path.insert(0, str(HANDOFF_ROOT))

from ai_grading_service import grade_answer


def main() -> None:
    """
    功能：读取一个样例 JSON 并调用单题批改入口，方便外包本地验证集成方式。

    参数：
        无

    返回：
        None
    """
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("sample_input.json")
    # 使用 utf-8-sig 是为了兼容 Windows 上偶尔带 BOM 的 JSON 文件。
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    result = grade_answer(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
