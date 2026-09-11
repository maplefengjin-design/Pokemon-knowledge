from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _local_llm_config(root: Path) -> dict[str, str]:
    """Read a small KEY=VALUE file without adding a dotenv dependency."""
    path = root / "config" / "llm.env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    database_path: Path = PROJECT_ROOT / "data" / "curated" / "pokemon_pilot.db"
    csv_dir: Path = PROJECT_ROOT / "data" / "raw" / "upstream" / "pokeapi" / "data" / "v2" / "csv"
    chinese_dataset_dir: Path = PROJECT_ROOT / "data" / "raw" / "upstream" / "pokemon-dataset-zh"
    pokemon_showdown_dir: Path = PROJECT_ROOT / "data" / "raw" / "upstream" / "pokemon-showdown"
    pilot_species_path: Path = PROJECT_ROOT / "config" / "pilot_species.json"
    mechanics_path: Path = PROJECT_ROOT / "config" / "mechanics_mainline.json"
    knowledge_passages_path: Path = PROJECT_ROOT / "config" / "knowledge_passages_mainline.json"
    move_mechanic_categories_path: Path = PROJECT_ROOT / "config" / "move_mechanic_categories.json"
    manifest_path: Path = PROJECT_ROOT / "data" / "manifests" / "pokeapi-pilot.json"
    audit_path: Path = PROJECT_ROOT / "data" / "manifests" / "pilot-audit.json"
    edition_id: str = "mainline"
    llm_enabled: bool = False
    llm_protocol: str = "responses"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 15.0
    llm_max_tool_rounds: int = 3
    llm_total_timeout_seconds: float = 35.0
    llm_trace: bool = False

    @property
    def llm_configured(self) -> bool:
        return self.llm_enabled and bool(self.llm_model and self.llm_base_url)

    @classmethod
    def from_env(cls) -> "Settings":
        root = PROJECT_ROOT
        local = _local_llm_config(root)

        def setting(name: str, default: str = "") -> str:
            return os.getenv(name, local.get(name, default))

        db_value = os.getenv("POKEMON_DB_PATH", "data/curated/pokemon_pilot.db")
        llm_api_key = (
            os.getenv("POKEMON_LLM_API_KEY")
            or local.get("POKEMON_LLM_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )
        db_path = Path(db_value)
        if not db_path.is_absolute():
            db_path = root / db_path
        return cls(
            project_root=root,
            database_path=db_path,
            edition_id=os.getenv("POKEMON_EDITION", "mainline"),
            llm_enabled=setting("POKEMON_LLM_ENABLED", "false").lower() in {"1", "true", "yes"},
            llm_protocol=setting("POKEMON_LLM_PROTOCOL", "responses"),
            llm_base_url=setting("POKEMON_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=llm_api_key,
            llm_model=setting("POKEMON_LLM_MODEL"),
            llm_timeout_seconds=float(setting("POKEMON_LLM_TIMEOUT_SECONDS", "15")),
            llm_max_tool_rounds=int(setting("POKEMON_LLM_MAX_TOOL_ROUNDS", "3")),
            llm_total_timeout_seconds=float(
                setting("POKEMON_LLM_TOTAL_TIMEOUT_SECONDS", "35")
            ),
            llm_trace=setting("POKEMON_LLM_TRACE", "false").lower() in {"1", "true", "yes"},
        )
