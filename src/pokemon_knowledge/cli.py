from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .audit import audit_pilot
from .build import build_pilot_database
from .config import Settings
from .evaluation import run_pilot_evaluation
from .query import AmbiguousEntityError, EntityNotFoundError, KnowledgeService, PilotDataUnavailableError


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pokemon-kb", description="Local, version-aware Pokémon knowledge CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build-mainline", help="Build the full main-series database from a pinned PokéAPI checkout")
    subparsers.add_parser("build-pilot", help="Compatibility alias for build-mainline")
    subparsers.add_parser("audit", help="Validate coverage and referential integrity")
    subparsers.add_parser("evaluate", help="Run the 50-case deterministic pilot evaluation")

    search = subparsers.add_parser("search", help="Resolve an exact name, alias or National Pokédex number")
    search.add_argument("query")
    search.add_argument("--language", default="zh-hans")

    species = subparsers.add_parser("species", help="Return a structured species summary")
    species.add_argument("query")
    species.add_argument("--language", default="zh-hans")

    learnset = subparsers.add_parser("learnset", help="Return a version-group-specific learnset")
    learnset.add_argument("query")
    learnset.add_argument("--version-group", help="Omit to use the species' latest available learnset")
    learnset.add_argument("--variant")
    learnset.add_argument("--language", default="zh-hans")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    try:
        if args.command in {"build-mainline", "build-pilot"}:
            _print(build_pilot_database(settings))
        elif args.command == "audit":
            report = audit_pilot(settings)
            _print(report)
            return 0 if report["ok"] else 1
        elif args.command == "evaluate":
            report = run_pilot_evaluation(settings)
            _print(report)
            return 0 if report["ok"] else 1
        elif args.command == "search":
            _print(KnowledgeService.from_settings(settings).resolve_species(args.query, args.language))
        elif args.command == "species":
            _print(KnowledgeService.from_settings(settings).species_summary(args.query, args.language))
        elif args.command == "learnset":
            _print(
                KnowledgeService.from_settings(settings).learnset(
                    args.query, args.version_group, args.language, args.variant
                )
            )
        return 0
    except AmbiguousEntityError as error:
        _print({"error": "ambiguous_entity", "query": error.query, "candidates": error.candidates})
        return 2
    except (EntityNotFoundError, PilotDataUnavailableError, FileNotFoundError, ValueError) as error:
        _print({"error": error.__class__.__name__, "message": str(error)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
