from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Raised when a local configuration file cannot be loaded safely."""


def load_project_env(
    env_path: str | Path = ENV_PATH,
    *,
    override: bool = False,
) -> bool:
    """Load a UTF-8 .env from an explicit path without import-time effects."""
    path = Path(env_path)
    if not path.exists():
        return False
    try:
        return load_dotenv(
            dotenv_path=path,
            override=override,
            encoding="utf-8-sig",
        )
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"配置文件不是有效的 UTF-8 编码：{path}。"
            "请在 VS Code 中选择“以编码重新打开”，转换为 UTF-8 后再试。"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(f"无法读取配置文件：{path}（{exc}）") from exc
