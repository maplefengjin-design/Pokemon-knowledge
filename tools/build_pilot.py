from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pokemon_knowledge.audit import audit_pilot  # noqa: E402
from pokemon_knowledge.build import build_pilot_database  # noqa: E402


if __name__ == "__main__":
    result = build_pilot_database()
    result["audit"] = audit_pilot()
    print(json.dumps(result, ensure_ascii=False, indent=2))

