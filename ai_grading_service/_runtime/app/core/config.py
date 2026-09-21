from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_ENV_FILE_LOADED = False


@dataclass(frozen=True)
class AppConfig:
    """
    功能：保存后端运行所需的基础路径配置。

    参数：
        dataclass: 自动生成初始化方法的数据类装饰器。

    返回：
        AppConfig: 包含项目根目录、数据库路径、项目数据目录、导出目录和 prompt 目录的配置对象。
    """

    project_root: Path
    database_path: Path
    project_data_dir: Path
    export_dir: Path
    prompt_dir: Path


def get_project_root() -> Path:
    """
    功能：根据当前文件位置反推出项目根目录。

    参数：
        无

    返回：
        Path: 项目根目录路径。
    """
    # config.py 位于 backend/app/core 下，因此 parents[3] 对应仓库根目录。
    return Path(__file__).resolve().parents[3]


def load_dotenv_file() -> None:
    """
    功能：从仓库根目录的 .env 文件读取本地环境变量。

    参数：
        无

    返回：
        None
    """
    global _ENV_FILE_LOADED
    if _ENV_FILE_LOADED:
        return

    _ENV_FILE_LOADED = True
    env_path = get_project_root() / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        cleaned_key = key.strip()
        cleaned_value = strip_env_value(value.strip())
        if not cleaned_key:
            continue

        # 已经在系统环境中显式设置的变量优先级更高，避免 .env 覆盖临时调试配置。
        os.environ.setdefault(cleaned_key, cleaned_value)


def strip_env_value(value: str) -> str:
    """
    功能：清理 .env 中变量值两侧的引号和空白。

    参数：
        value (str): .env 文件中等号右侧的原始值。

    返回：
        str: 可写入 os.environ 的变量值。
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def get_config() -> AppConfig:
    """
    功能：构造应用配置，并确保本地数据目录存在。

    参数：
        无

    返回：
        AppConfig: 后端运行配置。
    """
    load_dotenv_file()

    project_root = get_project_root()
    project_data_dir = project_root / "data" / "app_projects"
    export_dir = project_root / "data" / "exports"
    database_path = project_root / "data" / "ai_grading_platform.sqlite3"
    prompt_dir = project_root / "prompts"

    return AppConfig(
        project_root=project_root,
        database_path=database_path,
        project_data_dir=project_data_dir,
        export_dir=export_dir,
        prompt_dir=prompt_dir,
    )


def get_env_value(name: str, default: str = "") -> str:
    """
    功能：读取环境变量，并统一去掉首尾空白。

    参数：
        name (str): 环境变量名称。
        default (str): 环境变量不存在时使用的默认值。

    返回：
        str: 清理后的环境变量值。
    """
    load_dotenv_file()
    return os.getenv(name, default).strip()


def get_env_int(name: str, default: int) -> int:
    """
    功能：读取整数型环境变量，读取失败时回退到默认值。

    参数：
        name (str): 环境变量名称。
        default (int): 环境变量不存在或格式错误时的默认值。

    返回：
        int: 可直接用于超时、重试等配置的整数值。
    """
    raw_value = get_env_value(name, "")
    if not raw_value:
        return default

    try:
        return int(raw_value)
    except ValueError:
        # 本地演示环境里配置错误不应导致应用启动失败；真实调用时会在具体阶段暴露问题。
        return default
