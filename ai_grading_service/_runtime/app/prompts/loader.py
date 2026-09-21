from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_config


@dataclass(frozen=True)
class PromptSpec:
    """
    功能：描述一个流水线阶段使用的 prompt 文件和版本。

    参数：
        dataclass: 自动生成初始化方法的数据类装饰器。

    返回：
        PromptSpec: 包含阶段、文件名、版本等信息的不可变对象。
    """

    stage_type: str
    prompt_name: str
    prompt_version: str
    filename: str


PROMPT_REGISTRY: dict[str, PromptSpec] = {
    "visual_ocr": PromptSpec(
        stage_type="visual_ocr",
        prompt_name="visual_ocr",
        prompt_version="v2",
        filename="v2/visual_ocr.md",
    ),
    "evidence_risk": PromptSpec(
        stage_type="evidence_risk",
        prompt_name="evidence_risk",
        prompt_version="v2",
        filename="v2/evidence_risk.md",
    ),
    "method_analysis": PromptSpec(
        stage_type="method_analysis",
        prompt_name="method_analysis",
        prompt_version="v2",
        filename="v2/method_analysis.md",
    ),
    "scoring": PromptSpec(
        stage_type="scoring",
        prompt_name="scoring",
        prompt_version="v2.3",
        filename="v2/scoring/base.md",
    ),
    "review_decision": PromptSpec(
        stage_type="review_decision",
        prompt_name="review_decision",
        prompt_version="v2",
        filename="v2/review_decision.md",
    ),
    "homework_review_decision": PromptSpec(
        stage_type="homework_review_decision",
        prompt_name="homework_review_decision",
        prompt_version="v2.5",
        filename="v2/homework_review_decision.md",
    ),
    "rubric_gap_discovery": PromptSpec(
        stage_type="rubric_gap_discovery",
        prompt_name="rubric_gap_discovery",
        prompt_version="v2",
        filename="v2/rubric_gap_discovery.md",
    ),
    "question_image_parser": PromptSpec(
        stage_type="question_image_parser",
        prompt_name="question_image_parser",
        prompt_version="v2.6.1",
        filename="v2.6.1/question_generation/image_parser.md",
    ),
    "question_config_generator": PromptSpec(
        stage_type="question_config_generator",
        prompt_name="question_config_generator",
        prompt_version="v2.6.1",
        filename="v2.6.1/question_generation/config_generator.md",
    ),
    "question_output_formatter": PromptSpec(
        stage_type="question_output_formatter",
        prompt_name="question_output_formatter",
        prompt_version="v2.6.1",
        filename="v2.6.1/question_generation/output_formatter.md",
    ),
}


def get_prompt_spec(stage_type: str) -> PromptSpec:
    """
    功能：根据 stage_type 获取固定的 prompt 版本配置。

    参数：
        stage_type (str): 流水线阶段类型，例如 visual_ocr、grading、risk_gate。

    返回：
        PromptSpec: 该阶段对应的 prompt 配置。
    """
    try:
        return PROMPT_REGISTRY[stage_type]
    except KeyError as exc:
        raise ValueError(f"未配置阶段 prompt：{stage_type}") from exc


def load_prompt(stage_type: str) -> str:
    """
    功能：读取某个流水线阶段使用的 prompt 正文。

    参数：
        stage_type (str): 流水线阶段类型。

    返回：
        str: prompt 文件内容。
    """
    spec = get_prompt_spec(stage_type)
    prompt_path = get_config().prompt_dir / spec.filename

    if not prompt_path.exists():
        raise ValueError(f"prompt 文件不存在：{prompt_path}")

    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise ValueError(f"prompt 文件为空：{prompt_path}")

    # 这里不做模板变量替换，是为了先冻结 prompt 版本加载边界；具体输入拼装交给 provider。
    return prompt_text


def load_scoring_prompt(question_type: str) -> str:
    """
    功能：按题型组合 V2 scoring prompt。

    参数：
        question_type (str): 已规范化题型。

    返回：
        str: base + rule pack + output contract 的完整 prompt。
    """
    prompt_dir = get_config().prompt_dir
    base_path = prompt_dir / "v2" / "scoring" / "base.md"
    rule_path = prompt_dir / "v2" / "scoring" / "rule_packs" / f"{question_type}.md"
    contract_path = prompt_dir / "v2" / "scoring" / "output_contract.md"
    if not rule_path.exists():
        rule_path = prompt_dir / "v2" / "scoring" / "rule_packs" / "unknown.md"

    parts = []
    for path in [base_path, rule_path, contract_path]:
        if not path.exists():
            raise ValueError(f"prompt 文件不存在：{path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"prompt 文件为空：{path}")
        parts.append(text)

    return "\n\n---\n\n".join(parts)


def load_homework_review_prompt(question_type: str) -> str:
    """
    功能：按题型组合 v2.5 作业评价提示词。

    参数：
        question_type (str): 已规范化的题型。

    返回：
        str: base、题型规则和 output contract 组成的完整作业评价提示词。
    """
    prompt_dir = get_config().prompt_dir / "v2" / "homework_review"
    base_path = prompt_dir / "base.md"
    rule_path = prompt_dir / "rule_packs" / f"{question_type}.md"
    contract_path = prompt_dir / "output_contract.md"
    if not rule_path.exists():
        rule_path = prompt_dir / "rule_packs" / "unknown.md"

    parts = []
    for path in [base_path, rule_path, contract_path]:
        if not path.exists():
            raise ValueError(f"prompt 文件不存在：{path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"prompt 文件为空：{path}")
        parts.append(text)

    return "\n\n---\n\n".join(parts)
