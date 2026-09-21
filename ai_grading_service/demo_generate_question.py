from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HANDOFF_ROOT = Path(__file__).resolve().parents[1]
if str(HANDOFF_ROOT) not in sys.path:
    # 直接运行脚本时显式加入交付包根目录，保证可以按正式包名导入。
    sys.path.insert(0, str(HANDOFF_ROOT))

from ai_grading_service import generate_question_from_image, generate_question_from_text


def load_payload(input_path: Path) -> dict[str, Any]:
    """
    功能：读取并校验题目生成 Demo 使用的 JSON 入参。

    参数：
        input_path (Path): 输入 JSON 文件路径。

    返回：
        dict[str, Any]: 可传给题目生成公开方法的入参对象。
    """
    # 使用 utf-8-sig 兼容 Windows 工具保存的 BOM 文件。
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("输入文件顶层必须是 JSON 对象")
    return payload


def resolve_local_image_path(payload: dict[str, Any], input_path: Path) -> dict[str, Any]:
    """
    功能：把样例中的相对图片路径按输入 JSON 所在目录解析，避免调用目录改变导致找不到图片。

    参数：
        payload (dict[str, Any]): 原始题目生成入参。
        input_path (Path): 输入 JSON 文件路径。

    返回：
        dict[str, Any]: 图片路径已经规范化的入参副本。
    """
    normalized_payload = dict(payload)
    image_path = str(normalized_payload.get("image_path") or "").strip()
    if image_path:
        candidate = Path(image_path)
        if not candidate.is_absolute():
            normalized_payload["image_path"] = str((input_path.parent / candidate).resolve())
    return normalized_payload


def main() -> int:
    """
    功能：根据 JSON 是否包含图片字段自动调用图片或题干生成方法，并打印格式化结果。

    参数：
        无

    返回：
        int: 成功返回 0，参数错误返回 2，生成失败返回 1。
    """
    if len(sys.argv) != 2:
        print("用法：python ai_grading_service/demo_generate_question.py <input.json>")
        return 2

    input_path = Path(sys.argv[1]).resolve()
    payload = load_payload(input_path)
    if payload.get("image_path") or payload.get("image_url"):
        # 图片入口需要把相对路径定位到清单目录；URL 和文本入口无需额外处理。
        result = generate_question_from_image(resolve_local_image_path(payload, input_path))
    else:
        result = generate_question_from_text(payload)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
