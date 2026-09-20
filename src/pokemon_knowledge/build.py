from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from . import __version__
from .config import Settings
from .normalize import normalize_alias


SOURCE_ID = "pokeapi-csv"
REPOSITORY_URL = "https://github.com/PokeAPI/pokeapi"
ZH_SOURCE_ID = "pokemon-dataset-zh"
ZH_REPOSITORY_URL = "https://github.com/42arch/pokemon-dataset-zh"
ZH_DATA_FILES = ("ability_list.json", "move_list.json", "item_list.json")
SHOWDOWN_SOURCE_ID = "pokemon-showdown"
SHOWDOWN_REPOSITORY_URL = "https://github.com/smogon/pokemon-showdown"
WIKI_ABILITY_SOURCE_ID = "pokemon-encyclopedia-ability-infobox"
WIKI_ABILITY_SOURCE_URL = "https://wiki.52poke.com"
SHOWDOWN_DATA_FILES = (
    "moves.ts", "dex-moves.ts", "abilities.ts", "dex-abilities.ts", "conditions.ts", "LICENSE"
)

USED_CSV_FILES = (
    "abilities.csv",
    "ability_names.csv",
    "ability_flavor_text.csv",
    "ability_prose.csv",
    "egg_group_prose.csv",
    "egg_groups.csv",
    "evolution_trigger_prose.csv",
    "evolution_triggers.csv",
    "generations.csv",
    "languages.csv",
    "item_categories.csv",
    "item_category_prose.csv",
    "item_names.csv",
    "item_flavor_text.csv",
    "item_pocket_names.csv",
    "item_pockets.csv",
    "item_prose.csv",
    "items.csv",
    "machines.csv",
    "move_effect_prose.csv",
    "move_flavor_text.csv",
    "move_names.csv",
    "moves.csv",
    "pokemon.csv",
    "pokemon_abilities.csv",
    "pokemon_abilities_past.csv",
    "pokemon_egg_groups.csv",
    "pokemon_evolution.csv",
    "pokemon_form_names.csv",
    "pokemon_forms.csv",
    "pokemon_items.csv",
    "pokemon_move_method_prose.csv",
    "pokemon_move_methods.csv",
    "pokemon_moves.csv",
    "pokemon_species.csv",
    "pokemon_species_flavor_text.csv",
    "pokemon_species_names.csv",
    "pokemon_colors.csv",
    "pokemon_color_names.csv",
    "pokemon_shapes.csv",
    "pokemon_shape_prose.csv",
    "pokemon_habitats.csv",
    "pokemon_habitat_names.csv",
    "growth_rates.csv",
    "growth_rate_prose.csv",
    "pokemon_stats.csv",
    "pokemon_stats_past.csv",
    "pokemon_types.csv",
    "pokemon_types_past.csv",
    "stat_names.csv",
    "stats.csv",
    "type_names.csv",
    "types.csv",
    "version_groups.csv",
    "versions.csv",
    "regions.csv",
    "region_names.csv",
    "pokedexes.csv",
    "pokedex_prose.csv",
    "pokemon_dex_numbers.csv",
)

def _rows(csv_dir: Path, filename: str) -> Iterator[dict[str, str]]:
    with (csv_dir / filename).open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _integer(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _required_integer(value: str) -> int:
    return int(value)


def _parse_showdown_move_flags(path: Path) -> dict[int, set[str]]:
    """Extract move numbers and flags from Showdown's declarative move table.

    We parse only top-level object blocks and the two scalar fields required by
    this importer. No TypeScript code is executed.
    """
    text = path.read_text(encoding="utf-8")
    block_pattern = re.compile(
        r"(?ms)^\t(?:[A-Za-z0-9]+|\"[^\"]+\"): \{\r?\n(.*?)(?=^\t\},?\r?$)"
    )
    result: dict[int, set[str]] = {}
    for block in block_pattern.findall(text):
        number_match = re.search(r"(?m)^\t\tnum: (-?\d+),\s*$", block)
        flags_match = re.search(r"(?m)^\t\tflags: \{([^}]*)\},\s*$", block)
        if number_match is None or flags_match is None:
            continue
        move_id = int(number_match.group(1))
        if move_id <= 0:
            continue
        flags = {
            match.group(1)
            for match in re.finditer(r"([A-Za-z0-9]+): 1", flags_match.group(1))
        }
        result[move_id] = flags
    return result


def _parse_showdown_ability_signals(path: Path) -> dict[str, set[str]]:
    """Extract numbered abilities, rule flags, and top-level onStart handlers.

    The result is deliberately limited to declarative metadata used by the
    current battle engine.  TypeScript is parsed as text and is never executed.
    """
    text = path.read_text(encoding="utf-8")
    block_pattern = re.compile(
        r"(?ms)^\t(?P<identifier>[A-Za-z0-9]+|\"[^\"]+\"): "
        r"\{\r?\n(?P<body>.*?)(?=^\t\},?\r?$)"
    )
    result: dict[str, set[str]] = {}
    for match in block_pattern.finditer(text):
        identifier = match.group("identifier").strip('"')
        block = match.group("body")
        number_match = re.search(r"(?m)^\t\tnum: (-?\d+),\s*$", block)
        if number_match is None:
            continue
        if int(number_match.group(1)) <= 0:
            continue
        signals: set[str] = set()
        flags_match = re.search(r"(?s)(?:^|\n)\t\tflags:\s*\{(.*?)\},", block)
        if flags_match:
            signals.update(
                f"flag:{match.group(1)}"
                for match in re.finditer(r"([A-Za-z0-9]+): 1", flags_match.group(1))
            )
        if re.search(r"(?m)^\t\tonStart\(", block):
            signals.add("event:onStart")
        result[identifier] = signals
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_value(repo: Path, *args: str) -> str:
    command = [
        "git",
        "-c",
        f"safe.directory={repo.as_posix()}",
        "-C",
        str(repo),
        *args,
    ]
    return subprocess.check_output(command, text=True, encoding="utf-8").strip()


def _build_manifest(settings: Settings, pilot_ids: set[int]) -> tuple[dict[str, object], str]:
    repo = settings.csv_dir.parents[2]
    files: dict[str, dict[str, object]] = {}
    for filename in USED_CSV_FILES:
        path = settings.csv_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Required PokéAPI CSV is missing: {path}")
        files[filename] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}

    zh_commit_path = settings.chinese_dataset_dir / "commit.json"
    zh_data_dir = settings.chinese_dataset_dir / "data"
    zh_license_path = settings.chinese_dataset_dir / "LICENSE"
    for filename in (*ZH_DATA_FILES,):
        path = zh_data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Required Chinese dataset file is missing: {path}")
    if not zh_commit_path.exists() or not zh_license_path.exists():
        raise FileNotFoundError("Chinese dataset commit metadata or license is missing")
    zh_commit = json.loads(zh_commit_path.read_text(encoding="utf-8"))

    showdown_commit_path = settings.pokemon_showdown_dir / "commit.json"
    if not showdown_commit_path.exists():
        raise FileNotFoundError(f"Pokémon Showdown commit metadata is missing: {showdown_commit_path}")
    for filename in SHOWDOWN_DATA_FILES:
        path = settings.pokemon_showdown_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Required Pokémon Showdown source file is missing: {path}")
    if not settings.move_mechanic_categories_path.exists():
        raise FileNotFoundError(
            f"Move mechanism category mapping is missing: {settings.move_mechanic_categories_path}"
        )
    if not settings.ability_mechanic_categories_path.exists():
        raise FileNotFoundError(
            f"Ability mechanism category mapping is missing: "
            f"{settings.ability_mechanic_categories_path}"
        )
    if not settings.ability_infobox_path.exists():
        raise FileNotFoundError(
            f"Ability infobox snapshot is missing: {settings.ability_infobox_path}"
        )
    if not settings.battle_states_path.exists():
        raise FileNotFoundError(
            f"Battle-state mapping is missing: {settings.battle_states_path}"
        )
    showdown_commit = json.loads(showdown_commit_path.read_text(encoding="utf-8"))
    ability_infobox = json.loads(settings.ability_infobox_path.read_text(encoding="utf-8"))
    if ability_infobox.get("source_id") != WIKI_ABILITY_SOURCE_ID:
        raise ValueError("Ability infobox snapshot has an unexpected source_id")

    manifest: dict[str, object] = {
        "schema_version": 12,
        "source_id": SOURCE_ID,
        "repository_url": REPOSITORY_URL,
        "upstream_commit": _git_value(repo, "rev-parse", "HEAD"),
        "commit_time": _git_value(repo, "show", "-s", "--format=%cI"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "parser_version": __version__,
        "pilot_species_ids": sorted(pilot_ids),
        "files": files,
        "curation_files": {
            "mechanics_mainline.json": {
                "bytes": settings.mechanics_path.stat().st_size,
                "sha256": _sha256(settings.mechanics_path),
            },
            "knowledge_passages_mainline.json": {
                "bytes": settings.knowledge_passages_path.stat().st_size,
                "sha256": _sha256(settings.knowledge_passages_path),
            },
            "move_mechanic_categories.json": {
                "bytes": settings.move_mechanic_categories_path.stat().st_size,
                "sha256": _sha256(settings.move_mechanic_categories_path),
            },
            "ability_mechanic_categories.json": {
                "bytes": settings.ability_mechanic_categories_path.stat().st_size,
                "sha256": _sha256(settings.ability_mechanic_categories_path),
            },
            "ability_infobox_mainline.json": {
                "bytes": settings.ability_infobox_path.stat().st_size,
                "sha256": _sha256(settings.ability_infobox_path),
            },
            "battle_states_mainline.json": {
                "bytes": settings.battle_states_path.stat().st_size,
                "sha256": _sha256(settings.battle_states_path),
            },
        },
        "secondary_sources": {
            ZH_SOURCE_ID: {
                "repository_url": ZH_REPOSITORY_URL,
                "upstream_commit": zh_commit["sha"],
                "commit_time": zh_commit["commit"]["committer"]["date"],
                "license": "CC-BY-NC-SA-3.0 for source wiki text; repository wrapper is MIT",
                "files": {
                    filename: {
                        "bytes": (zh_data_dir / filename).stat().st_size,
                        "sha256": _sha256(zh_data_dir / filename),
                    }
                    for filename in ZH_DATA_FILES
                },
            },
            SHOWDOWN_SOURCE_ID: {
                "repository_url": SHOWDOWN_REPOSITORY_URL,
                "upstream_commit": showdown_commit["sha"],
                "commit_time": showdown_commit["commit"]["committer"]["date"],
                "license": "MIT",
                "role": "current_mainline_move_and_ability_mechanic_categories",
                "files": {
                    filename: {
                        "bytes": (settings.pokemon_showdown_dir / filename).stat().st_size,
                        "sha256": _sha256(settings.pokemon_showdown_dir / filename),
                    }
                    for filename in SHOWDOWN_DATA_FILES
                },
            },
            WIKI_ABILITY_SOURCE_ID: {
                "repository_url": WIKI_ABILITY_SOURCE_URL,
                "upstream_commit": ability_infobox["revision_digest"],
                "commit_time": ability_infobox["latest_revision_timestamp"],
                "license": ability_infobox["license"],
                "role": "current_mainline_ability_infobox_properties",
                "files": {
                    "ability_infobox_mainline.json": {
                        "bytes": settings.ability_infobox_path.stat().st_size,
                        "sha256": _sha256(settings.ability_infobox_path),
                    }
                },
            },
        },
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return manifest, hashlib.sha256(canonical).hexdigest()


def _insert_many(connection: sqlite3.Connection, sql: str, values: Iterable[tuple[object, ...]]) -> None:
    connection.executemany(sql, values)


def _flatten_item_nodes(nodes: list[dict[str, object]]) -> Iterator[dict[str, object]]:
    for node in nodes:
        if node.get("type") == "item":
            yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _flatten_item_nodes(children)


def _build_entity_tags(connection: sqlite3.Connection) -> tuple[int, int]:
    """Derive practical discovery tags from normalized structured facts."""
    tag_cache: dict[str, int] = {}

    def ensure_tag(key: str, category: str, label: str, description: str = "") -> int:
        if key in tag_cache:
            return tag_cache[key]
        tag_id = len(tag_cache) + 1
        connection.execute(
            "INSERT INTO tags VALUES (?, ?, ?, ?, ?)",
            (tag_id, key, category, label, description or label),
        )
        connection.execute(
            "INSERT INTO tags_fts VALUES (?, ?, ?, ?)",
            (tag_id, key, label, description or label),
        )
        tag_cache[key] = tag_id
        return tag_id

    def assign(
        entity_type: str,
        entity_id: int,
        key: str,
        category: str,
        label: str,
        evidence: dict[str, object],
        description: str = "",
        source_id: str = SOURCE_ID,
    ) -> None:
        tag_id = ensure_tag(key, category, label, description)
        connection.execute(
            "INSERT OR IGNORE INTO entity_tags VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entity_type,
                entity_id,
                tag_id,
                "deterministic_rule",
                1.0,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                source_id,
            ),
        )

    entity_labels = {
        "species": "宝可梦",
        "move": "招式",
        "ability": "特性",
        "item": "道具",
    }
    shape_labels = {
        "ball": "球状", "squiggle": "蛇形", "fish": "鱼形", "arms": "仅有手臂",
        "blob": "不定形", "upright": "直立形", "legs": "仅有腿部", "quadruped": "四足形",
        "wings": "有翅膀", "tentacles": "触手形", "heads": "多头形", "humanoid": "人形",
        "bug-wings": "虫翼形", "armor": "甲壳形",
    }
    habitat_labels = {
        "cave": "洞窟", "forest": "森林", "grassland": "草原", "mountain": "山地",
        "rare": "稀有地点", "rough-terrain": "崎岖地形", "sea": "海洋", "urban": "城镇",
        "waters-edge": "水边",
    }
    growth_labels = {
        "slow": "慢", "medium": "中等", "fast": "快", "medium-slow": "中等偏慢",
        "slow-then-very-fast": "前慢后快", "fast-then-very-slow": "前快后慢",
    }
    for row in connection.execute("SELECT entity_type, entity_id FROM entities"):
        entity_type = str(row["entity_type"])
        assign(
            entity_type,
            int(row["entity_id"]),
            f"entity:{entity_type}",
            "entity_class",
            entity_labels[entity_type],
            {"entity_type": entity_type},
        )

    for entity_type, table in (("species", "species"), ("move", "moves"), ("ability", "abilities")):
        for row in connection.execute(f"SELECT id, generation_id FROM {table} WHERE generation_id IS NOT NULL"):
            generation = int(row["generation_id"])
            label = f"第{generation}世代{entity_labels[entity_type]}"
            assign(
                entity_type,
                int(row["id"]),
                f"{entity_type}:generation:{generation}",
                "generation",
                label,
                {"generation_id": generation},
            )

    for row in connection.execute(
        """SELECT DISTINCT s.id, t.identifier,
                  COALESCE(zh.name, en.name, t.identifier) AS type_name
           FROM species s
           JOIN pokemon_variants p ON p.species_id = s.id AND p.is_default = 1
           JOIN pokemon_types pt ON pt.pokemon_id = p.id
           JOIN types t ON t.id = pt.type_id
           LEFT JOIN type_names zh ON zh.type_id = t.id AND zh.language_id = 12
           LEFT JOIN type_names en ON en.type_id = t.id AND en.language_id = 9"""
    ):
        assign(
            "species", int(row["id"]), f"species:type:{row['identifier']}", "type",
            f"{row['type_name']}属性宝可梦", {"type": row["identifier"], "form_scope": "default"},
        )

    for row in connection.execute(
        """SELECT DISTINCT s.id, a.identifier,
                  COALESCE(zh.name, en.name, a.identifier) AS ability_name
           FROM species s
           JOIN pokemon_variants p ON p.species_id = s.id
           JOIN pokemon_abilities pa ON pa.pokemon_id = p.id
           JOIN abilities a ON a.id = pa.ability_id
           LEFT JOIN ability_names zh ON zh.ability_id = a.id AND zh.language_id = 12
           LEFT JOIN ability_names en ON en.ability_id = a.id AND en.language_id = 9"""
    ):
        assign(
            "species", int(row["id"]), f"species:ability:{row['identifier']}", "ability",
            f"拥有{row['ability_name']}特性的宝可梦", {"ability": row["identifier"], "form_scope": "all"},
        )

    for row in connection.execute(
        """SELECT s.id, c.identifier AS color, g.identifier AS growth_rate,
                  sh.identifier AS shape, h.identifier AS habitat,
                  s.gender_rate, s.is_baby, s.is_legendary, s.is_mythical,
                  s.forms_switchable, s.has_gender_differences
           FROM species s
           LEFT JOIN pokemon_colors c ON c.id = s.color_id
           LEFT JOIN growth_rates g ON g.id = s.growth_rate_id
           LEFT JOIN pokemon_shapes sh ON sh.id = s.shape_id
           LEFT JOIN pokemon_habitats h ON h.id = s.habitat_id"""
    ):
        species_id = int(row["id"])
        if row["color"]:
            color_name = connection.execute(
                "SELECT name FROM pokemon_color_names WHERE color_id = (SELECT id FROM pokemon_colors WHERE identifier = ?) AND language_id = 12",
                (row["color"],),
            ).fetchone()
            label_value = color_name[0] if color_name else row["color"]
            assign("species", species_id, f"species:color:{row['color']}", "appearance", f"{label_value}宝可梦", {"color": row["color"]})
        if row["growth_rate"]:
            assign("species", species_id, f"species:growth:{row['growth_rate']}", "growth", f"{growth_labels.get(row['growth_rate'], row['growth_rate'])}成长速率的宝可梦", {"growth_rate": row["growth_rate"]})
        if row["shape"]:
            assign("species", species_id, f"species:shape:{row['shape']}", "appearance", f"{shape_labels.get(row['shape'], row['shape'])}外形的宝可梦", {"shape": row["shape"]})
        if row["habitat"]:
            assign("species", species_id, f"species:habitat:{row['habitat']}", "habitat", f"常见于{habitat_labels.get(row['habitat'], row['habitat'])}的宝可梦", {"habitat": row["habitat"]})
        gender_rate = int(row["gender_rate"])
        gender_key, gender_label = (
            ("genderless", "无性别宝可梦") if gender_rate == -1 else
            ("female-only", "仅雌性宝可梦") if gender_rate == 8 else
            ("male-only", "仅雄性宝可梦") if gender_rate == 0 else
            ("mixed", "同时存在雌雄性别的宝可梦")
        )
        assign("species", species_id, f"species:gender:{gender_key}", "gender", gender_label, {"gender_rate": gender_rate})
        status = "mythical" if row["is_mythical"] else "legendary" if row["is_legendary"] else "ordinary"
        status_label = {"mythical": "幻之宝可梦", "legendary": "传说的宝可梦", "ordinary": "非传说且非幻之宝可梦"}[status]
        assign("species", species_id, f"species:status:{status}", "status", status_label, {"is_legendary": bool(row["is_legendary"]), "is_mythical": bool(row["is_mythical"])})
        if row["is_baby"]:
            assign("species", species_id, "species:status:baby", "status", "幼年宝可梦", {"is_baby": True})
        if row["forms_switchable"]:
            assign("species", species_id, "species:form:switchable", "form", "形态可变化的宝可梦", {"forms_switchable": True})
        gender_difference = bool(row["has_gender_differences"])
        assign(
            "species", species_id,
            "species:appearance:gender-difference" if gender_difference else "species:appearance:no-gender-difference",
            "appearance",
            "拥有性别外观差异的宝可梦" if gender_difference else "没有性别外观差异的宝可梦",
            {"has_gender_differences": gender_difference},
        )

    for row in connection.execute(
        "SELECT species_id, genus FROM species_names WHERE language_id = 12 AND genus <> ''"
    ):
        assign("species", int(row["species_id"]), f"species:genus:{normalize_alias(row['genus'])}", "classification", row["genus"], {"genus": row["genus"], "language": "zh-hans"})

    for row in connection.execute(
        """SELECT s.id, COUNT(*) AS type_count
           FROM species s JOIN pokemon_variants p ON p.species_id = s.id AND p.is_default = 1
           JOIN pokemon_types pt ON pt.pokemon_id = p.id GROUP BY s.id"""
    ):
        dual = int(row["type_count"]) == 2
        assign(
            "species", int(row["id"]),
            "species:type-count:dual" if dual else "species:type-count:single",
            "type", "双属性宝可梦" if dual else "单属性宝可梦",
            {"type_count": int(row["type_count"]), "form_scope": "default"},
        )

    stat_labels = {
        "hp": "HP", "attack": "攻击", "defense": "防御", "special-attack": "特攻",
        "special-defense": "特防", "speed": "速度",
    }
    species_stats: dict[int, dict[str, tuple[int, int]]] = {}
    for row in connection.execute(
        """SELECT s.id AS species_id, st.identifier, ps.base_stat, ps.effort
           FROM species s
           JOIN pokemon_variants p ON p.species_id = s.id AND p.is_default = 1
           JOIN pokemon_stats ps ON ps.pokemon_id = p.id
           JOIN stats st ON st.id = ps.stat_id
           WHERE st.identifier IN ('hp','attack','defense','special-attack','special-defense','speed')"""
    ):
        species_stats.setdefault(int(row["species_id"]), {})[str(row["identifier"])] = (
            int(row["base_stat"]), int(row["effort"])
        )
    for species_id, values in species_stats.items():
        total = sum(value for value, _ in values.values())
        assign("species", species_id, f"species:bst:{total}", "base_stat", f"种族值总和{total}的宝可梦", {"base_stat_total": total})
        if total >= 600:
            assign("species", species_id, "species:stat:bst-high", "base_stat", "种族值总和不低于600的宝可梦", {"base_stat_total_gte": 600})
        for stat, (value, effort) in values.items():
            label = stat_labels[stat]
            assign("species", species_id, f"species:stat:{stat}:{value}", "base_stat", f"{label}种族值{value}的宝可梦", {"stat": stat, "value": value})
            if value >= 120:
                assign("species", species_id, f"species:stat:{stat}:high", "base_stat", f"高{label}宝可梦", {"stat": stat, "value_gte": 120})
            if effort > 0:
                assign("species", species_id, f"species:ev:{stat}:{effort}", "effort_yield", f"打倒后获得{effort}点{label}基础点数的宝可梦", {"stat": stat, "effort": effort})

    for row in connection.execute("SELECT species_id, COUNT(*) AS count FROM pokemon_variants GROUP BY species_id HAVING COUNT(*) > 1"):
        assign("species", int(row["species_id"]), "species:form:multiple", "form", "拥有多种形态的宝可梦", {"variant_count": int(row["count"])})
    region_labels = {"alola": "阿罗拉", "galar": "伽勒尔", "hisui": "洗翠", "paldea": "帕底亚"}
    for region, label in region_labels.items():
        for row in connection.execute("SELECT DISTINCT species_id FROM pokemon_variants WHERE identifier LIKE ?", (f"%-{region}%",)):
            assign("species", int(row[0]), f"species:form:region:{region}", "form", f"拥有{label}地区形态的宝可梦", {"region": region})
    for row in connection.execute("SELECT DISTINCT p.species_id FROM pokemon_forms f JOIN pokemon_variants p ON p.id = f.pokemon_id WHERE f.is_mega = 1"):
        assign("species", int(row[0]), "species:form:mega", "form", "可以超级进化的宝可梦", {"is_mega": True})
    for row in connection.execute("SELECT DISTINCT species_id FROM pokemon_variants WHERE identifier LIKE '%-gmax%'"):
        assign("species", int(row[0]), "species:form:gmax", "form", "拥有超极巨化形态的宝可梦", {"is_gmax": True})

    outgoing = {int(row[0]) for row in connection.execute("SELECT DISTINCT from_species_id FROM evolutions WHERE from_species_id IS NOT NULL")}
    incoming = {int(row[0]) for row in connection.execute("SELECT DISTINCT evolved_species_id FROM evolutions")}
    for row in connection.execute("SELECT id FROM species"):
        species_id = int(row[0])
        if species_id in outgoing:
            assign("species", species_id, "species:evolution:can-evolve", "evolution", "可以进化的宝可梦", {"has_outgoing_evolution": True})
        else:
            assign("species", species_id, "species:evolution:final", "evolution", "没有进化形的宝可梦", {"has_outgoing_evolution": False})
        if species_id in incoming:
            assign("species", species_id, "species:evolution:has-pre", "evolution", "拥有进化前形态的宝可梦", {"has_incoming_evolution": True})

    for row in connection.execute(
        """SELECT DISTINCT seg.species_id, eg.identifier,
                  COALESCE(zh.name, en.name, eg.identifier) AS egg_name
           FROM species_egg_groups seg JOIN egg_groups eg ON eg.id = seg.egg_group_id
           LEFT JOIN egg_group_names zh ON zh.egg_group_id = eg.id AND zh.language_id = 12
           LEFT JOIN egg_group_names en ON en.egg_group_id = eg.id AND en.language_id = 9"""
    ):
        assign("species", int(row["species_id"]), f"species:egg:{row['identifier']}", "egg_group", f"{row['egg_name']}蛋组宝可梦", {"egg_group": row["identifier"]})

    for row in connection.execute(
        """SELECT DISTINCT sdn.species_id, r.identifier AS region_identifier,
                  COALESCE(rzh.name, ren.name, r.identifier) AS region_name
           FROM species_dex_numbers sdn
           JOIN pokedexes pd ON pd.id = sdn.pokedex_id AND pd.is_main_series = 1
           JOIN regions r ON r.id = pd.region_id
           LEFT JOIN region_names rzh ON rzh.region_id = r.id AND rzh.language_id = 12
           LEFT JOIN region_names ren ON ren.region_id = r.id AND ren.language_id = 9"""
    ):
        assign(
            "species", int(row["species_id"]), f"species:dex-region:{row['region_identifier']}",
            "regional_pokedex", f"{row['region_name']}地区图鉴宝可梦",
            {"region": row["region_identifier"], "source_relation": "species_dex_numbers"},
        )

    damage_labels = {1: "变化招式", 2: "物理招式", 3: "特殊招式"}
    for row in connection.execute(
        """SELECT m.id, m.power, m.accuracy, m.priority, m.effect_chance,
                  m.damage_class_id, t.identifier AS type_identifier,
                  COALESCE(zh.name, en.name, t.identifier) AS type_name
           FROM moves m LEFT JOIN types t ON t.id = m.type_id
           LEFT JOIN type_names zh ON zh.type_id = t.id AND zh.language_id = 12
           LEFT JOIN type_names en ON en.type_id = t.id AND en.language_id = 9"""
    ):
        move_id = int(row["id"])
        if row["type_identifier"]:
            assign("move", move_id, f"move:type:{row['type_identifier']}", "type", f"{row['type_name']}属性招式", {"type": row["type_identifier"]})
        if row["damage_class_id"] in damage_labels:
            class_label = damage_labels[int(row["damage_class_id"])]
            assign("move", move_id, f"move:class:{row['damage_class_id']}", "move_class", class_label, {"damage_class_id": int(row["damage_class_id"])})
        if row["power"] is not None:
            power = int(row["power"])
            assign("move", move_id, f"move:power:{power}", "power", f"威力{power}的招式", {"power": power})
            band = "high" if power >= 100 else "medium" if power >= 60 else "low"
            band_label = {"high": "高威力招式", "medium": "中等威力招式", "low": "低威力招式"}[band]
            assign("move", move_id, f"move:power-band:{band}", "power", band_label, {"power": power})
        if int(row["priority"]) > 0:
            assign("move", move_id, "move:priority:positive", "priority", "先制招式", {"priority_gt": 0})
        elif int(row["priority"]) < 0:
            assign("move", move_id, "move:priority:negative", "priority", "负优先度招式", {"priority_lt": 0})
        if row["effect_chance"] is not None:
            assign("move", move_id, "move:effect:chance", "effect", "具有概率性附加效果的招式", {"effect_chance": int(row["effect_chance"])})

    for row in connection.execute(
        """SELECT mm.move_id, mm.category_identifier, mm.ruleset_scope,
                  mc.source_flag_identifier, mc.label_zh, mc.description_zh
           FROM move_mechanic_memberships mm
           JOIN move_mechanic_categories mc
             ON mc.identifier = mm.category_identifier
           ORDER BY mm.move_id, mm.category_identifier"""
    ):
        assign(
            "move",
            int(row["move_id"]),
            f"move:mechanic:{row['category_identifier']}",
            "move_mechanic",
            str(row["label_zh"]),
            {
                "source_flag": row["source_flag_identifier"],
                "ruleset_scope": row["ruleset_scope"],
            },
            str(row["description_zh"]),
            SHOWDOWN_SOURCE_ID,
        )

    for row in connection.execute(
        """SELECT ams.ability_id, ams.category_identifier, ams.state,
                  ams.source_signal_present, ams.ruleset_scope,
                  amc.source_signal, amc.positive_label_zh, amc.negative_label_zh,
                  amc.description_zh, ams.source_id
           FROM ability_mechanic_states ams
           JOIN ability_mechanic_categories amc
             ON amc.identifier = ams.category_identifier
           ORDER BY ams.ability_id, ams.category_identifier"""
    ):
        state = str(row["state"])
        label = (
            str(row["positive_label_zh"])
            if state == "yes"
            else str(row["negative_label_zh"])
        )
        assign(
            "ability",
            int(row["ability_id"]),
            f"ability:mechanic:{row['category_identifier']}:{state}",
            "ability_mechanic",
            label,
            {
                "category": row["category_identifier"],
                "state": state,
                "source_signal": row["source_signal"],
                "source_signal_present": bool(row["source_signal_present"]),
                "ruleset_scope": row["ruleset_scope"],
            },
            str(row["description_zh"]),
            str(row["source_id"]),
        )

    for row in connection.execute("SELECT id, is_main_series FROM abilities"):
        label = "主系列特性" if row["is_main_series"] else "非主系列特性"
        key = "main-series" if row["is_main_series"] else "non-main-series"
        assign("ability", int(row["id"]), f"ability:scope:{key}", "scope", label, {"is_main_series": bool(row["is_main_series"])})

    for row in connection.execute(
        """SELECT i.id, c.identifier AS category_identifier, p.identifier AS pocket_identifier,
                  COALESCE(czh.name, cen.name, c.identifier) AS category_name,
                  COALESCE(pzh.name, pen.name, p.identifier) AS pocket_name
           FROM items i JOIN item_categories c ON c.id = i.category_id
           JOIN item_pockets p ON p.id = c.pocket_id
           LEFT JOIN item_category_names czh ON czh.item_category_id = c.id AND czh.language_id = 12
           LEFT JOIN item_category_names cen ON cen.item_category_id = c.id AND cen.language_id = 9
           LEFT JOIN item_pocket_names pzh ON pzh.item_pocket_id = p.id AND pzh.language_id = 12
           LEFT JOIN item_pocket_names pen ON pen.item_pocket_id = p.id AND pen.language_id = 9"""
    ):
        item_id = int(row["id"])
        assign("item", item_id, f"item:category:{row['category_identifier']}", "item_category", f"{row['category_name']}类道具", {"category": row["category_identifier"]})
        assign("item", item_id, f"item:pocket:{row['pocket_identifier']}", "item_pocket", f"位于{row['pocket_name']}口袋的道具", {"pocket": row["pocket_identifier"]})

    return len(tag_cache), int(connection.execute("SELECT COUNT(*) FROM entity_tags").fetchone()[0])


def build_pilot_database(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or Settings.from_env()
    if not settings.csv_dir.exists():
        raise FileNotFoundError(
            f"PokéAPI CSV directory not found: {settings.csv_dir}. "
            "Run the documented sparse clone step first."
        )

    pilot_config = json.loads(settings.pilot_species_path.read_text(encoding="utf-8"))
    pilot_ids = {int(value) for value in pilot_config["species_ids"]}
    if len(pilot_ids) != 20:
        raise ValueError(f"Pilot must contain exactly 20 distinct species, got {len(pilot_ids)}")

    manifest, manifest_sha256 = _build_manifest(settings, pilot_ids)
    output = settings.database_path
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".building")
    if temporary.exists():
        temporary.unlink()

    connection = sqlite3.connect(temporary)
    connection.row_factory = sqlite3.Row
    try:
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        connection.executescript(schema)
        connection.execute(
            "INSERT INTO source_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                SOURCE_ID,
                REPOSITORY_URL,
                manifest["upstream_commit"],
                manifest["commit_time"],
                manifest["fetched_at"],
                __version__,
                manifest_sha256,
            ),
        )
        zh_source = manifest["secondary_sources"][ZH_SOURCE_ID]
        connection.execute(
            "INSERT INTO source_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ZH_SOURCE_ID,
                ZH_REPOSITORY_URL,
                zh_source["upstream_commit"],
                zh_source["commit_time"],
                manifest["fetched_at"],
                __version__,
                manifest_sha256,
            ),
        )
        showdown_source = manifest["secondary_sources"][SHOWDOWN_SOURCE_ID]
        connection.execute(
            "INSERT INTO source_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                SHOWDOWN_SOURCE_ID,
                SHOWDOWN_REPOSITORY_URL,
                showdown_source["upstream_commit"],
                showdown_source["commit_time"],
                manifest["fetched_at"],
                __version__,
                manifest_sha256,
            ),
        )
        wiki_ability_source = manifest["secondary_sources"][WIKI_ABILITY_SOURCE_ID]
        connection.execute(
            "INSERT INTO source_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                WIKI_ABILITY_SOURCE_ID,
                WIKI_ABILITY_SOURCE_URL,
                wiki_ability_source["upstream_commit"],
                wiki_ability_source["commit_time"],
                manifest["fetched_at"],
                __version__,
                manifest_sha256,
            ),
        )
        connection.executemany(
            "INSERT INTO source_licenses VALUES (?, ?, ?, ?, ?)",
            (
                (
                    SOURCE_ID,
                    "BSD-3-Clause",
                    "PokéAPI contributors; Pokémon trademarks belong to Nintendo.",
                    "https://github.com/PokeAPI/pokeapi/blob/master/LICENSE.md",
                    1,
                ),
                (
                    ZH_SOURCE_ID,
                    "CC-BY-NC-SA-3.0",
                    "中文补缺文本整理自神奇宝贝百科；使用时须署名、非商业并相同方式共享。",
                    "https://wiki.52poke.com/wiki/神奇宝贝百科:版权声明",
                    0,
                ),
                (
                    SHOWDOWN_SOURCE_ID,
                    "MIT",
                    "Pokémon Showdown © 2011–2026 Guangcong Luo and contributors.",
                    "https://github.com/smogon/pokemon-showdown/blob/master/LICENSE",
                    1,
                ),
                (
                    WIKI_ABILITY_SOURCE_ID,
                    "CC-BY-NC-SA-3.0",
                    "特性基本信息字段取自神奇宝贝百科的特性信息框；逐页保留修订号。",
                    "https://wiki.52poke.com/wiki/神奇宝贝百科:版权声明",
                    0,
                ),
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO languages VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    row["iso639"],
                    row["iso3166"],
                    _required_integer(row["official"]),
                )
                for row in _rows(settings.csv_dir, "languages.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO generations VALUES (?, ?)",
            ((_required_integer(row["id"]), row["identifier"]) for row in _rows(settings.csv_dir, "generations.csv")),
        )
        _insert_many(
            connection,
            "INSERT INTO version_groups VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    _required_integer(row["generation_id"]),
                    _integer(row["order"]),
                )
                for row in _rows(settings.csv_dir, "version_groups.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO game_versions VALUES (?, ?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"], _required_integer(row["version_group_id"]))
                for row in _rows(settings.csv_dir, "versions.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO regions VALUES (?, ?)",
            ((_required_integer(row["id"]), row["identifier"]) for row in _rows(settings.csv_dir, "regions.csv")),
        )
        _insert_many(
            connection,
            "INSERT INTO region_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["region_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "region_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokedexes VALUES (?, ?, ?, ?)",
            (
                (_required_integer(row["id"]), _integer(row["region_id"]), row["identifier"], _required_integer(row["is_main_series"]))
                for row in _rows(settings.csv_dir, "pokedexes.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokedex_names VALUES (?, ?, ?, ?)",
            (
                (_required_integer(row["pokedex_id"]), _required_integer(row["local_language_id"]), row["name"], row["description"] or None)
                for row in _rows(settings.csv_dir, "pokedex_prose.csv")
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO pokemon_colors VALUES (?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "pokemon_colors.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_color_names VALUES (?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_color_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                )
                for row in _rows(settings.csv_dir, "pokemon_color_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_shapes VALUES (?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "pokemon_shapes.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_shape_names VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_shape_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                    row["awesome_name"] or None,
                    row["description"] or None,
                )
                for row in _rows(settings.csv_dir, "pokemon_shape_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_habitats VALUES (?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "pokemon_habitats.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_habitat_names VALUES (?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_habitat_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                )
                for row in _rows(settings.csv_dir, "pokemon_habitat_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO growth_rates VALUES (?, ?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"], row["formula"])
                for row in _rows(settings.csv_dir, "growth_rates.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO growth_rate_names VALUES (?, ?, ?)",
            (
                (
                    _required_integer(row["growth_rate_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                )
                for row in _rows(settings.csv_dir, "growth_rate_prose.csv")
            ),
        )

        species_rows = list(_rows(settings.csv_dir, "pokemon_species.csv"))
        _insert_many(
            connection,
            """INSERT INTO species (
                id, identifier, generation_id, evolves_from_species_id, evolution_chain_id,
                color_id, shape_id, habitat_id,
                gender_rate, capture_rate, base_happiness, is_baby, hatch_counter, growth_rate_id,
                has_gender_differences, forms_switchable, is_legendary, is_mythical, source_id
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    _required_integer(row["generation_id"]),
                    _integer(row["evolution_chain_id"]),
                    _integer(row["color_id"]),
                    _integer(row["shape_id"]),
                    _integer(row["habitat_id"]),
                    _integer(row["gender_rate"]),
                    _integer(row["capture_rate"]),
                    _integer(row["base_happiness"]),
                    _required_integer(row["is_baby"]),
                    _integer(row["hatch_counter"]),
                    _integer(row["growth_rate_id"]),
                    _required_integer(row["has_gender_differences"]),
                    _required_integer(row["forms_switchable"]),
                    _required_integer(row["is_legendary"]),
                    _required_integer(row["is_mythical"]),
                    SOURCE_ID,
                )
                for row in species_rows
            ),
        )
        connection.executemany(
            "UPDATE species SET evolves_from_species_id = ? WHERE id = ?",
            (
                (_integer(row["evolves_from_species_id"]), _required_integer(row["id"]))
                for row in species_rows
                if row["evolves_from_species_id"]
            ),
        )
        connection.executemany("INSERT INTO pilot_species VALUES (?)", ((value,) for value in sorted(pilot_ids)))

        _insert_many(
            connection,
            "INSERT INTO species_names VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_species_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                    row["genus"] or None,
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_species_names.csv")
            ),
        )

        _insert_many(
            connection,
            "INSERT OR IGNORE INTO species_flavor_text VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["species_id"]),
                    _required_integer(row["version_id"]),
                    _required_integer(row["language_id"]),
                    row["flavor_text"],
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_species_flavor_text.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO species_dex_numbers VALUES (?, ?, ?)",
            (
                (_required_integer(row["species_id"]), _required_integer(row["pokedex_id"]), _required_integer(row["pokedex_number"]))
                for row in _rows(settings.csv_dir, "pokemon_dex_numbers.csv")
            ),
        )

        variant_rows = list(_rows(settings.csv_dir, "pokemon.csv"))
        pokemon_ids = {_required_integer(row["id"]) for row in variant_rows}
        _insert_many(
            connection,
            "INSERT INTO pokemon_variants VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    _required_integer(row["species_id"]),
                    _integer(row["height"]),
                    _integer(row["weight"]),
                    _integer(row["base_experience"]),
                    _required_integer(row["is_default"]),
                    SOURCE_ID,
                )
                for row in variant_rows
            ),
        )

        form_rows = [
            row for row in _rows(settings.csv_dir, "pokemon_forms.csv") if _required_integer(row["pokemon_id"]) in pokemon_ids
        ]
        form_ids = {_required_integer(row["id"]) for row in form_rows}
        _insert_many(
            connection,
            "INSERT INTO pokemon_forms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    row["form_identifier"] or None,
                    _required_integer(row["pokemon_id"]),
                    _integer(row["introduced_in_version_group_id"]),
                    _required_integer(row["is_default"]),
                    _required_integer(row["is_battle_only"]),
                    _required_integer(row["is_mega"]),
                    SOURCE_ID,
                )
                for row in form_rows
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_form_names VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_form_id"]),
                    _required_integer(row["local_language_id"]),
                    row["form_name"] or None,
                    row["pokemon_name"] or None,
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_form_names.csv")
                if _required_integer(row["pokemon_form_id"]) in form_ids
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO types VALUES (?, ?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"], _integer(row["generation_id"]))
                for row in _rows(settings.csv_dir, "types.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO type_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["type_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "type_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_types VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["type_id"]),
                    _required_integer(row["slot"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_types.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_types_past VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["generation_id"]),
                    _required_integer(row["type_id"]),
                    _required_integer(row["slot"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_types_past.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO stats VALUES (?, ?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"], _required_integer(row["is_battle_only"]))
                for row in _rows(settings.csv_dir, "stats.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO stat_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["stat_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "stat_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_stats VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["stat_id"]),
                    _required_integer(row["base_stat"]),
                    _required_integer(row["effort"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_stats.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_stats_past VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["generation_id"]),
                    _required_integer(row["stat_id"]),
                    _required_integer(row["base_stat"]),
                    _required_integer(row["effort"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_stats_past.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO abilities VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    _integer(row["generation_id"]),
                    _required_integer(row["is_main_series"]),
                )
                for row in _rows(settings.csv_dir, "abilities.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO ability_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["ability_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "ability_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT OR IGNORE INTO ability_prose VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["ability_id"]),
                    _required_integer(row["local_language_id"]),
                    row["short_effect"] or None,
                    row["effect"] or None,
                )
                for row in _rows(settings.csv_dir, "ability_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT OR IGNORE INTO ability_flavor_text VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["ability_id"]),
                    _required_integer(row["version_group_id"]),
                    _required_integer(row["language_id"]),
                    row["flavor_text"],
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "ability_flavor_text.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_abilities VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["ability_id"]),
                    _required_integer(row["is_hidden"]),
                    _required_integer(row["slot"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_abilities.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_abilities_past VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["generation_id"]),
                    _integer(row["ability_id"]),
                    _required_integer(row["is_hidden"]),
                    _required_integer(row["slot"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_abilities_past.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO egg_groups VALUES (?, ?)",
            ((_required_integer(row["id"]), row["identifier"]) for row in _rows(settings.csv_dir, "egg_groups.csv")),
        )
        _insert_many(
            connection,
            "INSERT INTO egg_group_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["egg_group_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "egg_group_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO species_egg_groups VALUES (?, ?, ?)",
            (
                (_required_integer(row["species_id"]), _required_integer(row["egg_group_id"]), SOURCE_ID)
                for row in _rows(settings.csv_dir, "pokemon_egg_groups.csv")
            ),
        )

        learnset_rows = _rows(settings.csv_dir, "pokemon_moves.csv")
        _insert_many(
            connection,
            "INSERT INTO moves VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    _integer(row["generation_id"]),
                    _integer(row["type_id"]),
                    _integer(row["power"]),
                    _integer(row["pp"]),
                    _integer(row["accuracy"]),
                    _required_integer(row["priority"]),
                    _integer(row["damage_class_id"]),
                    _integer(row["effect_id"]),
                    _integer(row["effect_chance"]),
                )
                for row in _rows(settings.csv_dir, "moves.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO move_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["move_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "move_names.csv")
            ),
        )
        move_category_payload = json.loads(
            settings.move_mechanic_categories_path.read_text(encoding="utf-8")
        )
        if int(move_category_payload.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported move mechanism category mapping schema")
        if move_category_payload.get("source_id") != SHOWDOWN_SOURCE_ID:
            raise ValueError("Move mechanism category mapping has an unexpected source_id")
        category_by_flag = {
            str(entry["source_flag"]): entry
            for entry in move_category_payload["categories"]
        }
        _insert_many(
            connection,
            "INSERT INTO move_mechanic_categories VALUES (?, ?, ?, ?, ?)",
            (
                (
                    str(entry["identifier"]),
                    str(entry["source_flag"]),
                    str(entry["label_zh"]),
                    str(entry["description_zh"]),
                    SHOWDOWN_SOURCE_ID,
                )
                for entry in move_category_payload["categories"]
            ),
        )
        showdown_flags = _parse_showdown_move_flags(settings.pokemon_showdown_dir / "moves.ts")
        observed_flags = set().union(*showdown_flags.values()) if showdown_flags else set()
        excluded_flags = set(move_category_payload.get("excluded_flags", {}))
        unclassified_flags = observed_flags - set(category_by_flag) - excluded_flags
        if unclassified_flags:
            raise ValueError(
                "Pokémon Showdown introduced unmapped move flags: "
                + ", ".join(sorted(unclassified_flags))
            )
        local_move_ids = {int(row[0]) for row in connection.execute("SELECT id FROM moves")}
        ruleset_scope = str(move_category_payload["ruleset_scope"])
        coverage_rows = [
            (
                move_id,
                ruleset_scope,
                f"data/moves.ts#move-num-{move_id}",
                SHOWDOWN_SOURCE_ID,
            )
            for move_id in sorted(showdown_flags)
            if move_id in local_move_ids
        ]
        _insert_many(
            connection,
            "INSERT INTO move_mechanic_coverage VALUES (?, ?, ?, ?)",
            coverage_rows,
        )
        membership_rows = [
            (
                move_id,
                str(category_by_flag[flag]["identifier"]),
                ruleset_scope,
                f"data/moves.ts#move-num-{move_id}",
                SHOWDOWN_SOURCE_ID,
            )
            for move_id, flags in showdown_flags.items()
            if move_id in local_move_ids
            for flag in sorted(flags)
            if flag in category_by_flag
        ]
        _insert_many(
            connection,
            "INSERT INTO move_mechanic_memberships VALUES (?, ?, ?, ?, ?)",
            membership_rows,
        )
        ability_category_payload = json.loads(
            settings.ability_mechanic_categories_path.read_text(encoding="utf-8")
        )
        if int(ability_category_payload.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported ability mechanism category mapping schema")
        if ability_category_payload.get("source_id") != SHOWDOWN_SOURCE_ID:
            raise ValueError("Ability mechanism category mapping has an unexpected source_id")
        ability_category_by_signal = {
            str(entry["source_signal"]): entry
            for entry in ability_category_payload["categories"]
        }
        _insert_many(
            connection,
            "INSERT INTO ability_mechanic_categories VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    str(entry["identifier"]),
                    str(entry["source_signal"]),
                    str(entry["positive_label_zh"]),
                    str(entry["negative_label_zh"]),
                    str(entry["description_zh"]),
                    int(bool(entry["positive_when_signal_present"])),
                    SHOWDOWN_SOURCE_ID,
                )
                for entry in ability_category_payload["categories"]
            ),
        )
        showdown_ability_signals = _parse_showdown_ability_signals(
            settings.pokemon_showdown_dir / "abilities.ts"
        )
        observed_ability_signals = (
            set().union(*showdown_ability_signals.values())
            if showdown_ability_signals
            else set()
        )
        unclassified_ability_signals = (
            observed_ability_signals
            - set(ability_category_by_signal)
            - set(ability_category_payload.get("excluded_signals", {}))
        )
        if unclassified_ability_signals:
            raise ValueError(
                "Pokémon Showdown introduced unmapped ability signals: "
                + ", ".join(sorted(unclassified_ability_signals))
            )
        local_main_series_abilities = {
            normalize_alias(str(row["identifier"])): (int(row["id"]), str(row["identifier"]))
            for row in connection.execute(
                "SELECT id, identifier FROM abilities WHERE is_main_series = 1"
            )
        }
        ability_ruleset_scope = str(ability_category_payload["ruleset_scope"])
        covered_abilities = []
        for local_key, (ability_id, local_identifier) in sorted(
            local_main_series_abilities.items()
        ):
            matching_identifiers = [
                identifier
                for identifier in showdown_ability_signals
                if normalize_alias(identifier) == local_key
            ]
            if not matching_identifiers:
                matching_identifiers = [
                    identifier
                    for identifier in showdown_ability_signals
                    if normalize_alias(identifier).startswith(local_key)
                ]
            if not matching_identifiers:
                continue
            signal_sets = {
                frozenset(showdown_ability_signals[identifier])
                for identifier in matching_identifiers
            }
            if len(signal_sets) != 1:
                raise ValueError(
                    f"Showdown ability variants disagree for {local_identifier}: "
                    + ", ".join(sorted(matching_identifiers))
                )
            locator_identifier = (
                matching_identifiers[0]
                if len(matching_identifiers) == 1
                else local_key + "-variants"
            )
            covered_abilities.append(
                (
                    ability_id,
                    local_identifier,
                    locator_identifier,
                    set(next(iter(signal_sets))),
                )
            )
        ability_coverage_rows = [
            (
                ability_id,
                ability_ruleset_scope,
                f"data/abilities.ts#ability-{showdown_identifier}",
                SHOWDOWN_SOURCE_ID,
            )
            for ability_id, _, showdown_identifier, _ in covered_abilities
        ]
        _insert_many(
            connection,
            "INSERT INTO ability_mechanic_coverage VALUES (?, ?, ?, ?)",
            ability_coverage_rows,
        )
        ability_state_rows = []
        for ability_id, _, showdown_identifier, signals in covered_abilities:
            for signal, entry in ability_category_by_signal.items():
                signal_present = signal in signals
                state_is_positive = (
                    signal_present == bool(entry["positive_when_signal_present"])
                )
                ability_state_rows.append(
                    (
                        ability_id,
                        str(entry["identifier"]),
                        "yes" if state_is_positive else "no",
                        int(signal_present),
                        ability_ruleset_scope,
                        f"data/abilities.ts#ability-{showdown_identifier}",
                        SHOWDOWN_SOURCE_ID,
                    )
                )
        _insert_many(
            connection,
            "INSERT INTO ability_mechanic_states VALUES (?, ?, ?, ?, ?, ?, ?)",
            ability_state_rows,
        )
        ability_infobox_payload = json.loads(
            settings.ability_infobox_path.read_text(encoding="utf-8")
        )
        if int(ability_infobox_payload.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported ability infobox snapshot schema")
        if ability_infobox_payload.get("source_id") != WIKI_ABILITY_SOURCE_ID:
            raise ValueError("Ability infobox snapshot has an unexpected source_id")
        wiki_categories = {
            str(entry["identifier"]): entry
            for entry in ability_infobox_payload["categories"]
        }
        _insert_many(
            connection,
            "INSERT INTO ability_mechanic_categories VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    str(entry["identifier"]),
                    f"wiki-infobox:{entry['source_field']}",
                    str(entry["positive_label_zh"]),
                    str(entry["negative_label_zh"]),
                    str(entry["description_zh"]),
                    1,
                    WIKI_ABILITY_SOURCE_ID,
                )
                for entry in ability_infobox_payload["categories"]
            ),
        )
        wiki_state_rows = []
        explicit_field_by_category = {
            str(entry["identifier"]): str(entry["source_field"])
            for entry in ability_infobox_payload["categories"]
        }
        for record in ability_infobox_payload["abilities"]:
            ability = connection.execute(
                "SELECT id FROM abilities WHERE identifier = ? AND is_main_series = 1",
                (str(record["identifier"]),),
            ).fetchone()
            if ability is None:
                raise ValueError(
                    f"Ability infobox identifier is not in PokeAPI: {record['identifier']}"
                )
            ability_id = int(ability["id"])
            explicit_fields = set(record.get("explicit_fields", []))
            source_locator = (
                "https://wiki.52poke.com/index.php?"
                f"curid={record['page_id']}&oldid={record['revision_id']}"
            )
            for category_identifier, state in record["states"].items():
                if category_identifier not in wiki_categories:
                    raise ValueError(
                        f"Unknown ability infobox category: {category_identifier}"
                    )
                wiki_state_rows.append(
                    (
                        ability_id,
                        str(category_identifier),
                        str(state),
                        int(
                            explicit_field_by_category[str(category_identifier)]
                            in explicit_fields
                        ),
                        str(ability_infobox_payload["ruleset_scope"]),
                        source_locator,
                        WIKI_ABILITY_SOURCE_ID,
                    )
                )
        _insert_many(
            connection,
            "INSERT INTO ability_mechanic_states VALUES (?, ?, ?, ?, ?, ?, ?)",
            wiki_state_rows,
        )
        _insert_many(
            connection,
            "INSERT OR IGNORE INTO move_effect_prose VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["move_effect_id"]),
                    _required_integer(row["local_language_id"]),
                    row["short_effect"] or None,
                    row["effect"] or None,
                )
                for row in _rows(settings.csv_dir, "move_effect_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT OR IGNORE INTO move_flavor_text VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["move_id"]),
                    _required_integer(row["version_group_id"]),
                    _required_integer(row["language_id"]),
                    row["flavor_text"],
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "move_flavor_text.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO move_methods VALUES (?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "pokemon_move_methods.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO move_method_names VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_move_method_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                    row["description"] or None,
                )
                for row in _rows(settings.csv_dir, "pokemon_move_method_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO learnsets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["version_group_id"]),
                    _required_integer(row["move_id"]),
                    _required_integer(row["pokemon_move_method_id"]),
                    _required_integer(row["level"]),
                    _integer(row["order"]),
                    _integer(row["mastery"]),
                    SOURCE_ID,
                )
                for row in learnset_rows
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO item_pockets VALUES (?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "item_pockets.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO item_pocket_names VALUES (?, ?, ?)",
            (
                (
                    _required_integer(row["item_pocket_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                )
                for row in _rows(settings.csv_dir, "item_pocket_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO item_categories VALUES (?, ?, ?)",
            (
                (_required_integer(row["id"]), _required_integer(row["pocket_id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "item_categories.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO item_category_names VALUES (?, ?, ?)",
            (
                (
                    _required_integer(row["item_category_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                )
                for row in _rows(settings.csv_dir, "item_category_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    row["identifier"],
                    _required_integer(row["category_id"]),
                    _required_integer(row["cost"]),
                    _integer(row["fling_power"]),
                    _integer(row["fling_effect_id"]),
                )
                for row in _rows(settings.csv_dir, "items.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO item_names VALUES (?, ?, ?)",
            (
                (_required_integer(row["item_id"]), _required_integer(row["local_language_id"]), row["name"])
                for row in _rows(settings.csv_dir, "item_names.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT OR IGNORE INTO item_prose VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["item_id"]),
                    _required_integer(row["local_language_id"]),
                    row["short_effect"] or None,
                    row["effect"] or None,
                )
                for row in _rows(settings.csv_dir, "item_prose.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT OR IGNORE INTO item_flavor_text VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["item_id"]),
                    _required_integer(row["version_group_id"]),
                    _required_integer(row["language_id"]),
                    row["flavor_text"],
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "item_flavor_text.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO pokemon_items VALUES (?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["pokemon_id"]),
                    _required_integer(row["version_id"]),
                    _required_integer(row["item_id"]),
                    _required_integer(row["rarity"]),
                    SOURCE_ID,
                )
                for row in _rows(settings.csv_dir, "pokemon_items.csv")
                if _required_integer(row["pokemon_id"]) in pokemon_ids
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO machines VALUES (?, ?, ?, ?)",
            (
                (
                    _required_integer(row["machine_number"]),
                    _required_integer(row["version_group_id"]),
                    _required_integer(row["item_id"]),
                    _required_integer(row["move_id"]),
                )
                for row in _rows(settings.csv_dir, "machines.csv")
            ),
        )

        _insert_many(
            connection,
            "INSERT INTO evolution_triggers VALUES (?, ?)",
            (
                (_required_integer(row["id"]), row["identifier"])
                for row in _rows(settings.csv_dir, "evolution_triggers.csv")
            ),
        )
        _insert_many(
            connection,
            "INSERT INTO evolution_trigger_names VALUES (?, ?, ?)",
            (
                (
                    _required_integer(row["evolution_trigger_id"]),
                    _required_integer(row["local_language_id"]),
                    row["name"],
                )
                for row in _rows(settings.csv_dir, "evolution_trigger_prose.csv")
            ),
        )
        condition_exclusions = {
            "id",
            "evolved_species_id",
            "evolution_trigger_id",
            "version_group_id",
            "is_default",
        }
        evolution_rows = [
            row
            for row in _rows(settings.csv_dir, "pokemon_evolution.csv")
        ]
        _insert_many(
            connection,
            "INSERT INTO evolutions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    _required_integer(row["id"]),
                    connection.execute(
                        "SELECT evolves_from_species_id FROM species WHERE id = ?",
                        (_required_integer(row["evolved_species_id"]),),
                    ).fetchone()[0],
                    _required_integer(row["evolved_species_id"]),
                    _required_integer(row["evolution_trigger_id"]),
                    _integer(row["version_group_id"]),
                    _required_integer(row["is_default"]),
                    json.dumps(
                        {key: (value if value != "" else None) for key, value in row.items() if key not in condition_exclusions},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    SOURCE_ID,
                )
                for row in evolution_rows
            ),
        )

        # Search is intentionally entity-centric.  Relations such as learnsets and
        # held items connect entities, but do not own their names or definitions.
        entity_specs = (
            ("species", "species", "species_names", "species_id"),
            ("ability", "abilities", "ability_names", "ability_id"),
            ("move", "moves", "move_names", "move_id"),
            ("item", "items", "item_names", "item_id"),
        )
        for entity_type, base_table, names_table, foreign_key in entity_specs:
            if entity_type == "species":
                connection.execute(
                    f"""INSERT INTO entities (entity_type, entity_id, identifier, detail_ready, source_id)
                        SELECT ?, b.id, b.identifier, 1, ?
                        FROM {base_table} b""",
                    (entity_type, SOURCE_ID),
                )
            else:
                connection.execute(
                    f"""INSERT INTO entities (entity_type, entity_id, identifier, detail_ready, source_id)
                        SELECT ?, id, identifier, 1, ? FROM {base_table}""",
                    (entity_type, SOURCE_ID),
                )
            connection.execute(
                """INSERT INTO entity_aliases
                       (entity_type, entity_id, alias_normalized, alias, alias_kind, language_id, source_id)
                   SELECT entity_type, entity_id, '', identifier, 'slug', NULL, source_id
                   FROM entities WHERE entity_type = ?""",
                (entity_type,),
            )
            for row in connection.execute(
                "SELECT entity_id, alias FROM entity_aliases WHERE entity_type = ? AND alias_kind = 'slug'",
                (entity_type,),
            ):
                connection.execute(
                    """UPDATE entity_aliases SET alias_normalized = ?
                       WHERE entity_type = ? AND entity_id = ? AND alias_kind = 'slug'""",
                    (normalize_alias(row["alias"]), entity_type, row["entity_id"]),
                )
            for row in connection.execute(
                f"SELECT {foreign_key} AS entity_id, language_id, name FROM {names_table}"
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO entity_aliases VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_type,
                        row["entity_id"],
                        normalize_alias(row["name"]),
                        row["name"],
                        "localized_name",
                        row["language_id"],
                        SOURCE_ID,
                    ),
                )

        for row in species_rows:
            species_id = _required_integer(row["id"])
            for alias, kind in (
                (str(species_id), "national_number"),
                (f"{species_id:04d}", "padded_number"),
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO entity_aliases VALUES (?, ?, ?, ?, ?, NULL, ?)",
                    ("species", species_id, normalize_alias(alias), alias, kind, SOURCE_ID),
                )

        zh_language_id = 12
        zh_data_dir = settings.chinese_dataset_dir / "data"
        zh_payloads: tuple[tuple[str, list[dict[str, object]]], ...] = (
            (
                "ability",
                json.loads((zh_data_dir / "ability_list.json").read_text(encoding="utf-8")),
            ),
            (
                "move",
                json.loads((zh_data_dir / "move_list.json").read_text(encoding="utf-8")),
            ),
            (
                "item",
                list(
                    _flatten_item_nodes(
                        json.loads((zh_data_dir / "item_list.json").read_text(encoding="utf-8"))
                    )
                ),
            ),
        )
        for entity_type, payload in zh_payloads:
            for record in payload:
                description_value = record.get("description")
                if isinstance(description_value, list):
                    description = "；".join(
                        str(value).strip() for value in description_value if str(value).strip()
                    )
                else:
                    description = str(description_value or "").strip()
                if not description:
                    continue

                entity_id: int | None = None
                for name_key in ("name_en", "name_zh"):
                    name = str(record.get(name_key) or "").strip()
                    if not name:
                        continue
                    matches = connection.execute(
                        """SELECT DISTINCT entity_id FROM entity_aliases
                           WHERE entity_type = ? AND alias_normalized = ?
                           ORDER BY entity_id""",
                        (entity_type, normalize_alias(name)),
                    ).fetchall()
                    if len(matches) == 1:
                        entity_id = int(matches[0][0])
                        break
                if entity_id is None:
                    continue
                chinese_name = str(record.get("name_zh") or "").strip()
                if chinese_name:
                    names_table, foreign_key = {
                        "ability": ("ability_names", "ability_id"),
                        "move": ("move_names", "move_id"),
                        "item": ("item_names", "item_id"),
                    }[entity_type]
                    connection.execute(
                        f"INSERT OR IGNORE INTO {names_table} ({foreign_key}, language_id, name) VALUES (?, ?, ?)",
                        (entity_id, zh_language_id, chinese_name),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO entity_aliases VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            entity_type,
                            entity_id,
                            normalize_alias(chinese_name),
                            chinese_name,
                            "encyclopedia_name",
                            zh_language_id,
                            ZH_SOURCE_ID,
                        ),
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO entity_descriptions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_type,
                        entity_id,
                        zh_language_id,
                        description,
                        "encyclopedia_summary",
                        f"data/{entity_type}_list.json#id={record.get('id', record.get('name_en', ''))}",
                        ZH_SOURCE_ID,
                    ),
                )

        mechanics_payload = json.loads(settings.mechanics_path.read_text(encoding="utf-8"))
        default_generation = int(mechanics_payload["default_generation"])
        version_row = connection.execute(
            "SELECT id FROM version_groups WHERE identifier = ?",
            (mechanics_payload["default_ruleset"],),
        ).fetchone()
        if version_row is None:
            raise ValueError(f"Unknown mechanics ruleset: {mechanics_payload['default_ruleset']}")
        for entry in mechanics_payload["entries"]:
            entity = connection.execute(
                "SELECT entity_id FROM entities WHERE entity_type = ? AND identifier = ?",
                (entry["entity_type"], entry["identifier"]),
            ).fetchall()
            if len(entity) != 1:
                raise ValueError(
                    f"Mechanics entity must resolve exactly once: {entry['entity_type']}:{entry['identifier']}"
                )
            entity_id = int(entity[0][0])
            source_file = "item_prose.csv" if entry["entity_type"] == "item" else "ability_prose.csv"
            connection.execute(
                "INSERT INTO mechanic_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["entity_type"],
                    entity_id,
                    default_generation,
                    int(version_row[0]),
                    entry["summary_zh"],
                    json.dumps(entry.get("parameters", {}), ensure_ascii=False, sort_keys=True),
                    f"{source_file}:{entry['identifier']}",
                    SOURCE_ID,
                    "第九世代标准主系列顶层值；由原库机制说明结构化整理。",
                ),
            )

        battle_state_payload = json.loads(
            settings.battle_states_path.read_text(encoding="utf-8")
        )
        if int(battle_state_payload.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported battle-state curation schema")
        if battle_state_payload.get("source_id") != SHOWDOWN_SOURCE_ID:
            raise ValueError("Battle-state curation must use the pinned Pokemon Showdown source")
        ruleset_scope = str(battle_state_payload["default_ruleset"])
        if connection.execute(
            "SELECT 1 FROM version_groups WHERE identifier = ?", (ruleset_scope,)
        ).fetchone() is None:
            raise ValueError(f"Unknown battle-state ruleset: {ruleset_scope}")

        category_ids: set[str] = set()
        for category in battle_state_payload["categories"]:
            category_identifier = str(category["identifier"])
            if category_identifier in category_ids:
                raise ValueError(f"Duplicate battle-state category: {category_identifier}")
            category_ids.add(category_identifier)
            connection.execute(
                "INSERT INTO battle_state_categories VALUES (?, ?, ?, ?, ?)",
                (
                    category_identifier,
                    category["label_zh"],
                    category["scope"],
                    category["description_zh"],
                    int(category["sort_order"]),
                ),
            )

        battle_state_count = 0
        battle_state_relation_count = 0
        for state in battle_state_payload["states"]:
            state_identifier = str(state["identifier"])
            category_identifier = str(state["category"])
            if category_identifier not in category_ids:
                raise ValueError(
                    f"Unknown category for battle state {state_identifier}: {category_identifier}"
                )
            connection.execute(
                "INSERT INTO battle_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    state_identifier,
                    state["name_zh"],
                    category_identifier,
                    state["subcategory"],
                    state["scope"],
                    state.get("generation_from"),
                    state.get("generation_to"),
                    int(bool(state.get("current", True))),
                    state["description_zh"],
                    state["mechanics_zh"],
                    state.get("counterplay_zh", ""),
                    json.dumps(state.get("parameters", {}), ensure_ascii=False, sort_keys=True),
                    ruleset_scope,
                    state["source_locator"],
                    SHOWDOWN_SOURCE_ID,
                ),
            )
            battle_state_count += 1
            aliases = [
                (state_identifier, "identifier"),
                (str(state["name_zh"]), "localized_name"),
                *((str(alias), "curated_alias") for alias in state.get("aliases", [])),
            ]
            for alias, alias_kind in aliases:
                normalized = normalize_alias(alias)
                if normalized:
                    connection.execute(
                        "INSERT OR IGNORE INTO battle_state_aliases VALUES (?, ?, ?, ?)",
                        (state_identifier, normalized, alias, alias_kind),
                    )
            for relation in state.get("relations", []):
                entity_rows = connection.execute(
                    "SELECT entity_id FROM entities WHERE entity_type = ? AND identifier = ?",
                    (relation["entity_type"], relation["identifier"]),
                ).fetchall()
                if len(entity_rows) != 1:
                    raise ValueError(
                        "Battle-state relation must resolve exactly once: "
                        f"{state_identifier} -> {relation['entity_type']}:{relation['identifier']}"
                    )
                connection.execute(
                    "INSERT INTO battle_state_relations VALUES (?, ?, ?, ?, ?)",
                    (
                        state_identifier,
                        relation["entity_type"],
                        int(entity_rows[0][0]),
                        relation["relation_kind"],
                        relation.get("note_zh", ""),
                    ),
                )
                battle_state_relation_count += 1

        knowledge_payload = json.loads(
            settings.knowledge_passages_path.read_text(encoding="utf-8")
        )
        if int(knowledge_payload.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported knowledge passage curation schema")
        for document in knowledge_payload["documents"]:
            passages = document.get("passages", [])
            document_content = "\n".join(
                str(passage["content"]).strip() for passage in passages
            )
            document_hash = hashlib.sha256(document_content.encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO knowledge_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document["document_id"],
                    document["title"],
                    zh_language_id,
                    document.get("edition_id", "mainline"),
                    document["source_name"],
                    document["source_url"],
                    document["source_kind"],
                    document["license_identifier"],
                    document["retrieved_at"],
                    document["source_revision"],
                    document_hash,
                ),
            )
            for order, passage in enumerate(passages, start=1):
                passage_id = passage["passage_id"]
                content = " ".join(str(passage["content"]).split())
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                version_group_id = None
                version_group = passage.get("version_group")
                if version_group:
                    version = connection.execute(
                        "SELECT id FROM version_groups WHERE identifier = ?", (version_group,)
                    ).fetchone()
                    if version is None:
                        raise ValueError(
                            f"Unknown passage version group: {version_group}"
                        )
                    version_group_id = int(version[0])
                connection.execute(
                    "INSERT INTO knowledge_passages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        passage_id,
                        document["document_id"],
                        passage["section_path"],
                        passage["heading"],
                        content,
                        passage.get("generation_from"),
                        passage.get("generation_to"),
                        version_group_id,
                        document.get("edition_id", "mainline"),
                        order,
                        content_hash,
                    ),
                )
                connection.execute(
                    "INSERT INTO knowledge_passages_fts VALUES (?, ?, ?, ?)",
                    (passage_id, passage["heading"], passage["section_path"], content),
                )
                for link in passage.get("entities", []):
                    entity = connection.execute(
                        "SELECT entity_id FROM entities WHERE entity_type = ? AND identifier = ?",
                        (link["entity_type"], link["identifier"]),
                    ).fetchall()
                    if len(entity) != 1:
                        raise ValueError(
                            "Passage entity must resolve exactly once: "
                            f"{link['entity_type']}:{link['identifier']}"
                        )
                    connection.execute(
                        "INSERT INTO passage_entities VALUES (?, ?, ?, ?)",
                        (
                            passage_id,
                            link["entity_type"],
                            int(entity[0][0]),
                            link.get("relation_kind", "subject"),
                        ),
                    )

        tag_count, entity_tag_count = _build_entity_tags(connection)
        connection.commit()
        violations = list(connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"Foreign-key validation failed: {violations[:5]}")
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        connection.close()

    os.replace(temporary, output)
    settings.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest["manifest_sha256"] = manifest_sha256
    settings.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "database": str(output),
        "manifest": str(settings.manifest_path),
        "upstream_commit": manifest["upstream_commit"],
        "pilot_species": len(pilot_ids),
        "detailed_species": len(species_rows),
        "tags": tag_count,
        "entity_tags": entity_tag_count,
        "move_mechanic_categories": len(category_by_flag),
        "moves_with_mechanic_coverage": len(coverage_rows),
        "move_mechanic_memberships": len(membership_rows),
        "ability_mechanic_categories": len(ability_category_by_signal) + len(wiki_categories),
        "abilities_with_mechanic_coverage": len(ability_coverage_rows),
        "ability_mechanic_states": len(ability_state_rows) + len(wiki_state_rows),
        "battle_states": battle_state_count,
        "battle_state_relations": battle_state_relation_count,
        "database_bytes": output.stat().st_size,
    }
