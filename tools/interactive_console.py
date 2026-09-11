from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pokemon_knowledge.audit import audit_pilot  # noqa: E402
from pokemon_knowledge.build import build_pilot_database  # noqa: E402
from pokemon_knowledge.chat import AnswerEngine  # noqa: E402
from pokemon_knowledge.config import Settings  # noqa: E402
from pokemon_knowledge.evaluation import run_pilot_evaluation  # noqa: E402
from pokemon_knowledge.llm import (  # noqa: E402
    LLMAnswerEngine,
    LLMClient,
    LLMConfigurationError,
    LLMRequestError,
)
from pokemon_knowledge.query import (  # noqa: E402
    AmbiguousEntityError,
    EntityNotFoundError,
    KnowledgeService,
    PilotDataUnavailableError,
)
from pokemon_knowledge.tool_api import KnowledgeTools  # noqa: E402


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _ensure_database(settings: Settings) -> None:
    if settings.database_path.exists():
        return
    print("未找到主系列知识库，正在从已同步的 PokéAPI CSV 自动构建……")
    result = build_pilot_database(settings)
    report = audit_pilot(settings)
    if not report["ok"]:
        raise RuntimeError(f"数据库构建后审计失败：{report['issues']}")
    print(f"数据库构建完成：{result['database']}")


def _default_variant(summary: dict[str, Any]) -> dict[str, Any]:
    return next(row for row in summary["variants"] if row["default"])


def _render_species(summary: dict[str, Any]) -> None:
    species = summary["species"]
    variant = _default_variant(summary)
    types = " / ".join(row["name"] for row in variant["types"])
    abilities = "、".join(
        f"{row['name']}{'（隐藏特性）' if row['hidden'] else ''}" for row in variant["abilities"]
    )
    eggs = " / ".join(row["name"] for row in summary["egg_groups"])
    stat_labels = {
        "hp": "HP",
        "attack": "攻击",
        "defense": "防御",
        "special-attack": "特攻",
        "special-defense": "特防",
        "speed": "速度",
    }

    print()
    print(f"{species['name']}  #{species['species_id']:04d}  ({species['identifier']})")
    print(f"版本：原作主系列当前值 | 初登场世代：第 {species['introduced_generation']} 世代")
    print(f"属性：{types}")
    print(f"特性：{abilities}")
    print(f"蛋组：{eggs}")
    print(f"身高：{variant['height_m']} m | 体重：{variant['weight_kg']} kg | 捕获率：{species['capture_rate']}")
    print(
        "种族值："
        + " / ".join(f"{stat_labels.get(key, key)} {value}" for key, value in variant["stats"].items())
        + f" | 总和 {variant['base_stat_total']}"
    )
    if len(summary["variants"]) > 1:
        identifiers = "、".join(row["identifier"] for row in summary["variants"] if not row["default"])
        print(f"其他形态/变体（{len(summary['variants']) - 1}）：{identifiers}")
    print(f"来源：{summary['source']['repository_url']} @ {summary['source']['upstream_commit'][:12]}")


def _render_candidates(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        print("未找到匹配的宝可梦。")
        return
    print()
    for row in candidates:
        entity_type = row.get("entity_type", "species")
        entity_id = row.get("entity_id", row.get("species_id"))
        ready = row.get("detail_ready", row.get("pilot_ready", True))
        readiness = "详情可用" if ready else "仅名称目录"
        print(f"- {entity_type} #{entity_id} {row['name']} ({row['identifier']}) [{readiness}]")


def _species_menu(service: KnowledgeService) -> None:
    query = input("请输入名称、英文名或全国图鉴编号：").strip()
    if not query:
        return
    _render_species(service.species_summary(query))


def _search_menu(service: KnowledgeService) -> None:
    query = input("请输入名称、简称或编号：").strip()
    if query:
        _render_candidates(service.resolve_species(query))


def _learnset_menu(service: KnowledgeService) -> None:
    query = input("请输入宝可梦名称或编号：").strip()
    if not query:
        return
    version_group = input("请输入版本组（留空则自动选择该宝可梦最新可用版本）：").strip() or None
    result = service.learnset(query, version_group)
    print()
    print(f"{result['pokemon_identifier']} | 版本组：{result['version_group']} | 共 {len(result['moves'])} 条")
    current_method: str | None = None
    for move in result["moves"]:
        if move["method_identifier"] != current_method:
            current_method = move["method_identifier"]
            print(f"\n[{current_method}]")
        level = f"Lv.{move['level']} " if current_method == "level-up" else ""
        print(f"  {level}{move['move_name']} ({move['move_identifier']})")
    print("\n来源：PokéAPI 社区整理数据；结果仅代表指定原作版本组。")


def _audit_menu(settings: Settings) -> None:
    report = audit_pilot(settings)
    status = "通过" if report["ok"] else "失败"
    print(f"审计：{status}")
    for name, value in report["counts"].items():
        print(f"  {name}: {value}")
    if report["issues"]:
        print(f"问题：{report['issues']}")


def _evaluation_menu(settings: Settings) -> None:
    result = run_pilot_evaluation(settings)
    print(f"黄金断言：{result['passed']}/{result['total']} 通过，准确率 {result['accuracy']:.1%}")
    if result["failures"]:
        print(f"失败项：{result['failures']}")


def _write_llm_trace(
    settings: Settings, question: str, engine: LLMAnswerEngine
) -> None:
    """Persist debugging details without exposing them in the user-facing chat."""
    log_dir = settings.project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": settings.llm_model,
        "protocol": settings.llm_protocol,
        "question": question,
        "tool_calls": engine.last_trace,
    }
    with (log_dir / "llm-tool-trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _run_interactive(settings: Settings) -> int:
    service = KnowledgeService.from_settings(settings)
    if settings.llm_enabled:
        client = LLMClient.from_settings(settings)
        engine: AnswerEngine | LLMAnswerEngine = LLMAnswerEngine(
            client,
            KnowledgeTools(service),
            max_tool_rounds=settings.llm_max_tool_rounds,
        )
        mode_text = f"大模型增强模式：{settings.llm_model}（{settings.llm_protocol}）"
    else:
        engine = AnswerEngine(service)
        mode_text = "本地规则模式：不会调用付费大模型 API"
    print("=" * 58)
    print("Pokemon 万能知识问答 — 官方版全图鉴")
    print(f"直接输入问题即可；当前为{mode_text}。")
    print("示例：皮卡丘的种族值是多少？ / 十万伏特是什么招式？")
    print("      静电特性有什么效果？ / 吃剩的东西是什么道具？")
    print("      皮卡丘在朱紫能学冲浪吗？ / 超梦和铁面忍者谁更快？")
    print("      紫色且超能力属性的宝可梦有哪些？ / 土居忍士的进化型是什么？")
    print("命令：/help  /audit  /eval  /exit")
    print("=" * 58)

    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        try:
            if question.casefold() in {"/exit", "exit", "quit", "退出"}:
                print("已退出。")
                return 0
            if question == "/help":
                print("助手：可以查询、比较或筛选宝可梦，也可询问进化链、标签、招式、特性和道具；学习面问题请注明版本。输入 /mode 查看当前回答模式。")
            elif question == "/mode":
                print(f"助手：当前为{mode_text}。")
            elif question == "/audit":
                _audit_menu(settings)
            elif question == "/eval":
                _evaluation_menu(settings)
            else:
                print(f"助手：{engine.answer(question)}")
                if isinstance(engine, LLMAnswerEngine) and settings.llm_trace:
                    _write_llm_trace(settings, question, engine)
        except AmbiguousEntityError as error:
            print("助手：名称存在多个候选，请使用完整名称或编号：")
            _render_candidates(error.candidates)
        except PilotDataUnavailableError as error:
            print(f"资料范围提示：{error}")
        except (EntityNotFoundError, ValueError) as error:
            print(f"查询失败：{error}")
        except LLMRequestError as error:
            print(f"大模型调用失败：{error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pokemon knowledge base Windows launcher")
    parser.add_argument("--check", action="store_true", help="Check startup prerequisites without opening chat")
    args = parser.parse_args(argv)
    _configure_console()
    settings = Settings.from_env()
    try:
        _ensure_database(settings)
        report = audit_pilot(settings, write_report=False)
        if not report["ok"]:
            print(f"启动检查失败：{report['issues']}", file=sys.stderr)
            return 1
        if args.check:
            print(
                f"启动检查通过：数据库={settings.database_path}，"
                f"详细物种={report['counts']['detailed_species']}，外键问题=0"
            )
            return 0
        return _run_interactive(settings)
    except (FileNotFoundError, RuntimeError, LLMConfigurationError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        print("如果尚未同步 PokéAPI，请先双击项目中的同步脚本或运行 tools\\sync_pokeapi.ps1。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
