from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .query import KnowledgeService


def run_pilot_evaluation(settings: Settings | None = None, cases_path: Path | None = None) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    cases_path = cases_path or settings.project_root / "evals" / "pilot_queries.json"
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    service = KnowledgeService.from_settings(settings)
    failures: list[dict[str, Any]] = []

    for case in payload["cases"]:
        actual: Any
        if case["kind"] == "resolve":
            results = service.resolve_species(case["query"])
            actual = results[0]["identifier"] if len(results) == 1 else [row["identifier"] for row in results]
        else:
            summary = service.species_summary(case["query"])
            default = next(row for row in summary["variants"] if row["default"])
            if case["kind"] == "default_types":
                actual = [row["identifier"] for row in default["types"]]
            elif case["kind"] == "default_stat":
                actual = default["stats"][case["field"]]
            else:
                raise ValueError(f"Unsupported evaluation case: {case['kind']}")
        if actual != case["expected"]:
            failures.append({"id": case["id"], "expected": case["expected"], "actual": actual})

    total = len(payload["cases"])
    passed = total - len(failures)
    return {
        "ok": not failures,
        "total": total,
        "passed": passed,
        "failed": len(failures),
        "accuracy": passed / total if total else 0,
        "failures": failures,
    }

