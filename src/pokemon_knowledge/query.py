from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .database import connect_readonly
from .normalize import normalize_alias


METADATA_ZH = {
    "pokemon_shapes": {
        "ball": "球状", "squiggle": "蛇形", "fish": "鱼形", "arms": "仅有手臂",
        "blob": "不定形", "upright": "直立形", "legs": "仅有腿部", "quadruped": "四足形",
        "wings": "有翅膀", "tentacles": "触手形", "heads": "多头形", "humanoid": "人形",
        "bug-wings": "虫翼形", "armor": "甲壳形",
    },
    "pokemon_habitats": {
        "cave": "洞窟", "forest": "森林", "grassland": "草原", "mountain": "山地",
        "rare": "稀有地点", "rough-terrain": "崎岖地形", "sea": "海洋", "urban": "城镇",
        "waters-edge": "水边",
    },
    "growth_rates": {
        "slow": "慢", "medium": "中等", "fast": "快", "medium-slow": "中等偏慢",
        "slow-then-very-fast": "前慢后快", "fast-then-very-slow": "前快后慢",
    },
}


FORM_FALLBACK_ZH = {
    "alola": "阿罗拉的样子",
    "galar": "伽勒尔的样子",
    "hisui": "洗翠的样子",
    "paldea": "帕底亚的样子",
    "gmax": "超极巨化形态",
    "mega": "超级进化形态",
    "mega-x": "超级进化Ｘ形态",
    "mega-y": "超级进化Ｙ形态",
    "mega-z": "超级进化Ｚ形态",
    "female": "雌性形态",
    "origin": "起源形态",
    "therian": "灵兽形态",
    "totem": "霸主形态",
    "starter": "搭档形态",
    "battle-bond": "牵绊变身形态",
    "terastal": "太晶形态",
    "stellar": "星晶形态",
}


def _variant_display_name(
    species_name: str,
    identifier: str,
    forms: list[dict[str, Any]],
    language: str,
) -> str:
    """Return a user-facing form name without leaking an internal identifier in Chinese."""
    localized = next((row.get("name") for row in forms if row.get("name")), None)
    if localized:
        return str(localized) if species_name in str(localized) else f"{species_name}（{localized}）"
    if language != "zh-hans":
        return identifier
    form_identifier = next(
        (str(row["form_identifier"]) for row in forms if row.get("form_identifier")), ""
    )
    label = FORM_FALLBACK_ZH.get(form_identifier)
    if label:
        return f"{species_name}（{label}）"
    if any(row.get("mega") for row in forms):
        return f"{species_name}（超级进化形态）"
    return f"{species_name}（特殊形态）"


class EntityNotFoundError(LookupError):
    pass


class AmbiguousEntityError(LookupError):
    def __init__(self, query: str, candidates: list[dict[str, Any]]) -> None:
        super().__init__(f"Ambiguous entity alias: {query}")
        self.query = query
        self.candidates = candidates


class PilotDataUnavailableError(LookupError):
    pass


def _character_ngrams(value: str, size: int) -> set[str]:
    normalized = normalize_alias(value)
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _fts_match_query(value: str) -> str | None:
    """Build a safe broad-candidate query for the FTS5 trigram tokenizer."""
    terms: set[str] = set()
    for chunk in re.findall(r"[\u3400-\u9fff]+|[A-Za-z0-9_-]+", value.casefold()):
        if len(chunk) < 3:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", chunk):
            terms.update(chunk[index : index + 3] for index in range(len(chunk) - 2))
        else:
            terms.add(chunk)
    if not terms:
        return None
    escaped = [f'"{term.replace(chr(34), chr(34) * 2)}"' for term in sorted(terms)]
    return " OR ".join(escaped)


@dataclass(frozen=True)
class KnowledgeService:
    database_path: Path

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "KnowledgeService":
        selected = settings or Settings.from_env()
        return cls(selected.database_path)

    def _connect(self) -> sqlite3.Connection:
        return connect_readonly(self.database_path)

    @staticmethod
    def _language_id(connection: sqlite3.Connection, language: str) -> int:
        row = connection.execute("SELECT id FROM languages WHERE identifier = ?", (language,)).fetchone()
        if row is None:
            raise ValueError(f"Unsupported language: {language}")
        return int(row[0])

    @staticmethod
    def _species_label(connection: sqlite3.Connection, species_id: int, language_id: int) -> str:
        row = connection.execute(
            """SELECT COALESCE(preferred.name, english.name, s.identifier)
               FROM species s
               LEFT JOIN species_names preferred
                 ON preferred.species_id = s.id AND preferred.language_id = ?
               LEFT JOIN species_names english
                 ON english.species_id = s.id AND english.language_id = 9
               WHERE s.id = ?""",
            (language_id, species_id),
        ).fetchone()
        return str(row[0])

    @staticmethod
    def _entity_label(
        connection: sqlite3.Connection, entity_type: str, entity_id: int, language_id: int
    ) -> str:
        metadata = {
            "species": ("species", "species_names", "species_id"),
            "move": ("moves", "move_names", "move_id"),
            "ability": ("abilities", "ability_names", "ability_id"),
            "item": ("items", "item_names", "item_id"),
        }
        try:
            base_table, names_table, foreign_key = metadata[entity_type]
        except KeyError as error:
            raise ValueError(f"Unsupported entity type: {entity_type}") from error
        row = connection.execute(
            f"""SELECT COALESCE(preferred.name, english.name, b.identifier)
                FROM {base_table} b
                LEFT JOIN {names_table} preferred
                  ON preferred.{foreign_key} = b.id AND preferred.language_id = ?
                LEFT JOIN {names_table} english
                  ON english.{foreign_key} = b.id AND english.language_id = 9
                WHERE b.id = ?""",
            (language_id, entity_id),
        ).fetchone()
        if row is None:
            raise EntityNotFoundError(f"{entity_type} id not found: {entity_id}")
        return str(row[0])

    def resolve_entity(
        self, query: str, entity_type: str | None = None, language: str = "zh-hans"
    ) -> list[dict[str, Any]]:
        normalized = normalize_alias(query)
        if not normalized:
            return []
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            parameters: list[object] = [normalized]
            type_clause = ""
            if entity_type is not None:
                type_clause = "AND e.entity_type = ?"
                parameters.append(entity_type)
            rows = connection.execute(
                f"""SELECT DISTINCT e.entity_type, e.entity_id, e.identifier, e.detail_ready
                    FROM entity_aliases a
                    JOIN entities e
                      ON e.entity_type = a.entity_type AND e.entity_id = a.entity_id
                    WHERE a.alias_normalized = ? {type_clause}
                    ORDER BY e.entity_type, e.entity_id""",
                parameters,
            ).fetchall()
            return [
                {
                    "entity_type": row["entity_type"],
                    "entity_id": int(row["entity_id"]),
                    "identifier": row["identifier"],
                    "name": self._entity_label(
                        connection, row["entity_type"], int(row["entity_id"]), language_id
                    ),
                    "detail_ready": bool(row["detail_ready"]),
                }
                for row in rows
            ]

    def resolve_species(self, query: str, language: str = "zh-hans") -> list[dict[str, Any]]:
        return [
            {
                "species_id": row["entity_id"],
                "identifier": row["identifier"],
                "name": row["name"],
                "pilot_ready": row["detail_ready"],
            }
            for row in self.resolve_entity(query, "species", language)
        ]

    def _resolve_one(self, connection: sqlite3.Connection, query: str, language_id: int) -> sqlite3.Row:
        normalized = normalize_alias(query)
        rows = connection.execute(
            """SELECT DISTINCT s.*, e.detail_ready AS pilot_ready
               FROM entity_aliases a
               JOIN entities e
                 ON e.entity_type = a.entity_type AND e.entity_id = a.entity_id
               JOIN species s ON s.id = e.entity_id
               WHERE a.entity_type = 'species' AND a.alias_normalized = ?
               ORDER BY s.id""",
            (normalized,),
        ).fetchall()
        if not rows:
            raise EntityNotFoundError(f"Species not found: {query}")
        if len(rows) > 1:
            candidates = [
                {
                    "species_id": int(row["id"]),
                    "identifier": row["identifier"],
                    "name": self._species_label(connection, int(row["id"]), language_id),
                    "pilot_ready": bool(row["pilot_ready"]),
                }
                for row in rows
            ]
            raise AmbiguousEntityError(query, candidates)
        if not rows[0]["pilot_ready"]:
            raise PilotDataUnavailableError(
                f"Species {rows[0]['identifier']} exists in the catalog but its detail rows are unavailable."
            )
        return rows[0]

    @staticmethod
    def _localized_dictionary(
        connection: sqlite3.Connection,
        base_table: str,
        names_table: str,
        foreign_key: str,
        ids: list[int],
        language_id: int,
    ) -> dict[int, dict[str, str]]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""SELECT b.id, b.identifier,
                       COALESCE(preferred.name, english.name, b.identifier) AS display_name
                FROM {base_table} b
                LEFT JOIN {names_table} preferred
                  ON preferred.{foreign_key} = b.id AND preferred.language_id = ?
                LEFT JOIN {names_table} english
                  ON english.{foreign_key} = b.id AND english.language_id = 9
                WHERE b.id IN ({placeholders})""",
            [language_id, *ids],
        ).fetchall()
        return {
            int(row["id"]): {"identifier": row["identifier"], "name": row["display_name"]}
            for row in rows
        }

    def species_summary(self, query: str, language: str = "zh-hans") -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            species = self._resolve_one(connection, query, language_id)
            species_id = int(species["id"])
            name_metadata = connection.execute(
                """SELECT preferred.genus AS genus
                   FROM species s
                   LEFT JOIN species_names preferred
                     ON preferred.species_id = s.id AND preferred.language_id = ?
                   WHERE s.id = ?""",
                (language_id, species_id),
            ).fetchone()
            flavor = connection.execute(
                """SELECT sft.flavor_text, gv.identifier AS game_version,
                          vg.identifier AS version_group
                   FROM species_flavor_text sft
                   JOIN game_versions gv ON gv.id = sft.version_id
                   JOIN version_groups vg ON vg.id = gv.version_group_id
                   WHERE sft.species_id = ? AND sft.language_id = ?
                   ORDER BY COALESCE(vg.sort_order, vg.id) DESC, gv.id DESC
                   LIMIT 1""",
                (species_id, language_id),
            ).fetchone()

            def optional_metadata(
                base_table: str, names_table: str, foreign_key: str, value: int | None
            ) -> dict[str, Any] | None:
                if value is None:
                    return None
                resolved = self._localized_dictionary(
                    connection, base_table, names_table, foreign_key, [int(value)], language_id
                )
                metadata = dict(resolved[int(value)], id=int(value))
                if language == "zh-hans":
                    metadata["name"] = METADATA_ZH.get(base_table, {}).get(
                        metadata["identifier"], metadata["name"]
                    )
                return metadata

            color = optional_metadata(
                "pokemon_colors", "pokemon_color_names", "color_id", species["color_id"]
            )
            shape = optional_metadata(
                "pokemon_shapes", "pokemon_shape_names", "shape_id", species["shape_id"]
            )
            habitat = optional_metadata(
                "pokemon_habitats", "pokemon_habitat_names", "habitat_id", species["habitat_id"]
            )
            growth_rate = optional_metadata(
                "growth_rates", "growth_rate_names", "growth_rate_id", species["growth_rate_id"]
            )
            variant_rows = connection.execute(
                "SELECT * FROM pokemon_variants WHERE species_id = ? ORDER BY is_default DESC, id",
                (species_id,),
            ).fetchall()
            pokemon_ids = [int(row["id"]) for row in variant_rows]

            type_rows = connection.execute(
                f"SELECT * FROM pokemon_types WHERE pokemon_id IN ({','.join('?' for _ in pokemon_ids)}) ORDER BY pokemon_id, slot",
                pokemon_ids,
            ).fetchall()
            type_ids = sorted({int(row["type_id"]) for row in type_rows})
            type_names = self._localized_dictionary(
                connection, "types", "type_names", "type_id", type_ids, language_id
            )

            stat_rows = connection.execute(
                f"""SELECT ps.pokemon_id, s.identifier, ps.base_stat, ps.effort
                    FROM pokemon_stats ps JOIN stats s ON s.id = ps.stat_id
                    WHERE ps.pokemon_id IN ({','.join('?' for _ in pokemon_ids)})
                    ORDER BY ps.pokemon_id, s.id""",
                pokemon_ids,
            ).fetchall()

            ability_rows = connection.execute(
                f"""SELECT pa.pokemon_id, pa.ability_id, pa.is_hidden, pa.slot
                    FROM pokemon_abilities pa
                    WHERE pa.pokemon_id IN ({','.join('?' for _ in pokemon_ids)})
                    ORDER BY pa.pokemon_id, pa.slot""",
                pokemon_ids,
            ).fetchall()
            ability_ids = sorted({int(row["ability_id"]) for row in ability_rows})
            ability_names = self._localized_dictionary(
                connection, "abilities", "ability_names", "ability_id", ability_ids, language_id
            )

            type_by_pokemon: dict[int, list[dict[str, Any]]] = {value: [] for value in pokemon_ids}
            for row in type_rows:
                entry = dict(type_names[int(row["type_id"])])
                entry["slot"] = int(row["slot"])
                type_by_pokemon[int(row["pokemon_id"])].append(entry)

            stats_by_pokemon: dict[int, dict[str, int]] = {value: {} for value in pokemon_ids}
            for row in stat_rows:
                stats_by_pokemon[int(row["pokemon_id"])][row["identifier"]] = int(row["base_stat"])

            abilities_by_pokemon: dict[int, list[dict[str, Any]]] = {value: [] for value in pokemon_ids}
            for row in ability_rows:
                entry = dict(ability_names[int(row["ability_id"])])
                entry.update({"hidden": bool(row["is_hidden"]), "slot": int(row["slot"])})
                abilities_by_pokemon[int(row["pokemon_id"])].append(entry)

            forms_by_pokemon: dict[int, list[dict[str, Any]]] = {value: [] for value in pokemon_ids}
            form_rows = connection.execute(
                f"""SELECT f.id, f.identifier, f.form_identifier, f.pokemon_id,
                           f.introduced_in_version_group_id, f.is_default,
                           f.is_battle_only, f.is_mega,
                           COALESCE(preferred.form_name, preferred.pokemon_name) AS localized_name
                    FROM pokemon_forms f
                    LEFT JOIN pokemon_form_names preferred
                      ON preferred.form_id = f.id AND preferred.language_id = ?
                    WHERE f.pokemon_id IN ({','.join('?' for _ in pokemon_ids)})
                    ORDER BY f.pokemon_id, f.id""",
                [language_id, *pokemon_ids],
            ).fetchall()
            for row in form_rows:
                forms_by_pokemon[int(row["pokemon_id"])].append(
                    {
                        "form_id": int(row["id"]),
                        "identifier": row["identifier"],
                        "form_identifier": row["form_identifier"],
                        "introduced_in_version_group_id": row["introduced_in_version_group_id"],
                        "default": bool(row["is_default"]),
                        "battle_only": bool(row["is_battle_only"]),
                        "mega": bool(row["is_mega"]),
                        "name": row["localized_name"],
                    }
                )

            variants = []
            species_display_name = self._species_label(connection, species_id, language_id)
            for row in variant_rows:
                pokemon_id = int(row["id"])
                stats = stats_by_pokemon[pokemon_id]
                forms = forms_by_pokemon[pokemon_id]
                variants.append(
                    {
                        "pokemon_id": pokemon_id,
                        "identifier": row["identifier"],
                        "name": (
                            species_display_name
                            if bool(row["is_default"])
                            else _variant_display_name(
                                species_display_name, row["identifier"], forms, language
                            )
                        ),
                        "default": bool(row["is_default"]),
                        "height_m": None if row["height_dm"] is None else float(row["height_dm"]) / 10,
                        "weight_kg": None if row["weight_hg"] is None else float(row["weight_hg"]) / 10,
                        "base_experience": row["base_experience"],
                        "types": type_by_pokemon[pokemon_id],
                        "stats": stats,
                        "base_stat_total": sum(stats.values()),
                        "abilities": abilities_by_pokemon[pokemon_id],
                        "forms": forms,
                    }
                )

            egg_rows = connection.execute(
                "SELECT egg_group_id FROM species_egg_groups WHERE species_id = ? ORDER BY egg_group_id",
                (species_id,),
            ).fetchall()
            egg_ids = [int(row[0]) for row in egg_rows]
            egg_names = self._localized_dictionary(
                connection, "egg_groups", "egg_group_names", "egg_group_id", egg_ids, language_id
            )

            evolution_rows = connection.execute(
                """SELECT e.*, fs.identifier AS from_identifier, ts.identifier AS to_identifier,
                          et.identifier AS trigger_identifier
                   FROM evolutions e
                   LEFT JOIN species fs ON fs.id = e.from_species_id
                   JOIN species ts ON ts.id = e.evolved_species_id
                   JOIN evolution_triggers et ON et.id = e.trigger_id
                   WHERE e.from_species_id = ? OR e.evolved_species_id = ?
                   ORDER BY e.id""",
                (species_id, species_id),
            ).fetchall()
            evolutions = [
                {
                    "from_species_id": row["from_species_id"],
                    "from_identifier": row["from_identifier"],
                    "to_species_id": int(row["evolved_species_id"]),
                    "to_identifier": row["to_identifier"],
                    "trigger": row["trigger_identifier"],
                    "version_group_id": row["version_group_id"],
                    "conditions": json.loads(row["conditions_json"]),
                }
                for row in evolution_rows
            ]
            dex_rows = connection.execute(
                """SELECT pd.identifier AS pokedex_identifier, sdn.pokedex_number,
                          r.identifier AS region_identifier,
                          COALESCE(rn_pref.name, rn_en.name, r.identifier) AS region_name,
                          COALESCE(pn_pref.name, pn_en.name, pd.identifier) AS pokedex_name
                   FROM species_dex_numbers sdn
                   JOIN pokedexes pd ON pd.id = sdn.pokedex_id
                   LEFT JOIN regions r ON r.id = pd.region_id
                   LEFT JOIN region_names rn_pref ON rn_pref.region_id = r.id AND rn_pref.language_id = ?
                   LEFT JOIN region_names rn_en ON rn_en.region_id = r.id AND rn_en.language_id = 9
                   LEFT JOIN pokedex_names pn_pref ON pn_pref.pokedex_id = pd.id AND pn_pref.language_id = ?
                   LEFT JOIN pokedex_names pn_en ON pn_en.pokedex_id = pd.id AND pn_en.language_id = 9
                   WHERE sdn.species_id = ? AND pd.is_main_series = 1
                   ORDER BY pd.id""",
                (language_id, language_id, species_id),
            ).fetchall()

            source = dict(connection.execute("SELECT * FROM source_snapshots WHERE source_id = ?", ("pokeapi-csv",)).fetchone())
            return {
                "edition_id": "mainline",
                "data_scope": "current values plus separately stored historical rows",
                "species": {
                    "species_id": species_id,
                    "identifier": species["identifier"],
                    "name": species_display_name,
                    "introduced_generation": int(species["generation_id"]),
                    "genus": name_metadata["genus"],
                    "description": None if flavor is None else " ".join(str(flavor["flavor_text"]).split()),
                    "description_game_version": None if flavor is None else flavor["game_version"],
                    "description_version_group": None if flavor is None else flavor["version_group"],
                    "color": color,
                    "shape": shape,
                    "habitat": habitat,
                    "growth_rate": growth_rate,
                    "gender_rate": species["gender_rate"],
                    "capture_rate": species["capture_rate"],
                    "base_happiness": species["base_happiness"],
                    "hatch_counter": species["hatch_counter"],
                    "baby": bool(species["is_baby"]),
                    "legendary": bool(species["is_legendary"]),
                    "mythical": bool(species["is_mythical"]),
                },
                "variants": variants,
                "egg_groups": [dict(egg_names[value], egg_group_id=value) for value in egg_ids],
                "evolutions": evolutions,
                "pokedex_memberships": [dict(row) for row in dex_rows],
                "source": source,
            }

    @staticmethod
    def _select_learnset_version(
        connection: sqlite3.Connection,
        pokemon_id: int,
        requested_version_group: str | None,
    ) -> tuple[sqlite3.Row, str, list[dict[str, Any]]]:
        available_rows = connection.execute(
            """SELECT vg.id, vg.identifier, vg.generation_id, vg.sort_order,
                      COUNT(*) AS move_record_count
               FROM learnsets l
               JOIN version_groups vg ON vg.id = l.version_group_id
               WHERE l.pokemon_id = ?
               GROUP BY vg.id
               ORDER BY COALESCE(vg.sort_order, vg.id) DESC, vg.id DESC""",
            (pokemon_id,),
        ).fetchall()
        available = [dict(row) for row in available_rows]
        if requested_version_group:
            selected = connection.execute(
                "SELECT * FROM version_groups WHERE identifier = ?",
                (requested_version_group,),
            ).fetchone()
            if selected is None:
                raise ValueError(f"Unknown version group: {requested_version_group}")
            return selected, "user_specified", available
        if not available_rows:
            raise PilotDataUnavailableError(
                f"No learnset version is available for pokemon_id={pokemon_id}"
            )
        selected = connection.execute(
            "SELECT * FROM version_groups WHERE id = ?", (available_rows[0]["id"],)
        ).fetchone()
        return selected, "auto_latest_available", available

    def learnset(
        self,
        query: str,
        version_group: str | None = None,
        language: str = "zh-hans",
        variant: str | None = None,
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            species = self._resolve_one(connection, query, language_id)
            if variant:
                pokemon = connection.execute(
                    "SELECT * FROM pokemon_variants WHERE species_id = ? AND identifier = ?",
                    (species["id"], variant),
                ).fetchone()
            else:
                pokemon = connection.execute(
                    "SELECT * FROM pokemon_variants WHERE species_id = ? AND is_default = 1",
                    (species["id"],),
                ).fetchone()
            if pokemon is None:
                raise EntityNotFoundError(f"Variant not found for {query}: {variant}")
            version, version_selection, available_versions = self._select_learnset_version(
                connection, int(pokemon["id"]), version_group
            )
            rows = connection.execute(
                """SELECT l.level, l.sort_order, m.identifier AS move_identifier,
                          method.identifier AS method_identifier,
                          COALESCE(mn_pref.name, mn_en.name, m.identifier) AS move_name
                   FROM learnsets l
                   JOIN moves m ON m.id = l.move_id
                   JOIN move_methods method ON method.id = l.method_id
                   LEFT JOIN move_names mn_pref ON mn_pref.move_id = m.id AND mn_pref.language_id = ?
                   LEFT JOIN move_names mn_en ON mn_en.move_id = m.id AND mn_en.language_id = 9
                   WHERE l.pokemon_id = ? AND l.version_group_id = ?
                   ORDER BY method.identifier, l.level, l.sort_order, m.identifier""",
                (language_id, pokemon["id"], version["id"]),
            ).fetchall()
            return {
                "edition_id": "mainline",
                "species_id": int(species["id"]),
                "pokemon_identifier": pokemon["identifier"],
                "requested_version_group": version_group,
                "version_group": version["identifier"],
                "version_selection": version_selection,
                "latest_available_version_group": available_versions[0]["identifier"],
                "available_version_groups": available_versions,
                "status": "available" if rows else "unavailable_in_version",
                "status_zh": (
                    "已返回该宝可梦在此版本的学习面。"
                    if rows
                    else "该指定版本没有此宝可梦的学习面记录；这通常表示它未在该版本登场，并非全局数据缺失。"
                ),
                "moves": [dict(row) for row in rows],
                "source_id": "pokeapi-csv",
            }

    def _resolve_entity_one(
        self, connection: sqlite3.Connection, query: str, entity_type: str, language_id: int
    ) -> sqlite3.Row:
        rows = connection.execute(
            """SELECT DISTINCT e.*
               FROM entity_aliases a
               JOIN entities e
                 ON e.entity_type = a.entity_type AND e.entity_id = a.entity_id
               WHERE a.entity_type = ? AND a.alias_normalized = ?
               ORDER BY e.entity_id""",
            (entity_type, normalize_alias(query)),
        ).fetchall()
        if not rows:
            raise EntityNotFoundError(f"{entity_type} not found: {query}")
        if len(rows) > 1:
            candidates = [
                {
                    "entity_type": entity_type,
                    "entity_id": int(row["entity_id"]),
                    "identifier": row["identifier"],
                    "name": self._entity_label(
                        connection, entity_type, int(row["entity_id"]), language_id
                    ),
                    "detail_ready": bool(row["detail_ready"]),
                }
                for row in rows
            ]
            raise AmbiguousEntityError(query, candidates)
        return rows[0]

    @staticmethod
    def _effect_text(
        connection: sqlite3.Connection,
        table: str,
        key_name: str,
        key_value: int | None,
        language_id: int,
        effect_chance: int | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        if key_value is None:
            return None, None, None
        row = connection.execute(
            f"""SELECT short_effect, effect, language_id FROM {table}
                WHERE {key_name} = ? AND language_id IN (?, 9)
                ORDER BY CASE WHEN language_id = ? THEN 0 ELSE 1 END LIMIT 1""",
            (key_value, language_id, language_id),
        ).fetchone()
        if row is None:
            return None, None, None
        chance = "?" if effect_chance is None else str(effect_chance)
        short_effect = row["short_effect"].replace("$effect_chance", chance) if row["short_effect"] else None
        effect = row["effect"].replace("$effect_chance", chance) if row["effect"] else None
        language = connection.execute(
            "SELECT identifier FROM languages WHERE id = ?", (row["language_id"],)
        ).fetchone()[0]
        return short_effect, effect, language

    @staticmethod
    def _flavor_text(
        connection: sqlite3.Connection,
        table: str,
        key_name: str,
        key_value: int,
        language_id: int,
    ) -> tuple[str, str, str, str] | None:
        row = connection.execute(
            f"""SELECT ft.flavor_text, l.identifier AS language,
                       vg.identifier AS version_group, ft.source_id
                FROM {table} ft
                JOIN languages l ON l.id = ft.language_id
                JOIN version_groups vg ON vg.id = ft.version_group_id
                WHERE ft.{key_name} = ? AND ft.language_id = ?
                ORDER BY COALESCE(vg.sort_order, vg.id) DESC, vg.id DESC
                LIMIT 1""",
            (key_value, language_id),
        ).fetchone()
        if row is None:
            return None
        text = " ".join(str(row["flavor_text"]).split())
        return text, row["language"], row["version_group"], row["source_id"]

    @staticmethod
    def _entity_description(
        connection: sqlite3.Connection, entity_type: str, entity_id: int, language_id: int
    ) -> tuple[str, str, str, str] | None:
        row = connection.execute(
            """SELECT d.description, d.description_kind, d.source_id, d.source_locator,
                      l.identifier AS language
               FROM entity_descriptions d
               JOIN languages l ON l.id = d.language_id
               WHERE d.entity_type = ? AND d.entity_id = ? AND d.language_id = ?
               ORDER BY CASE d.description_kind WHEN 'encyclopedia_summary' THEN 0 ELSE 1 END
               LIMIT 1""",
            (entity_type, entity_id, language_id),
        ).fetchone()
        if row is None:
            return None
        return row["description"], row["language"], row["source_id"], row["source_locator"]

    @staticmethod
    def _mechanics(
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: int,
        generation_id: int | None = None,
    ) -> dict[str, Any] | None:
        parameters: list[object] = [entity_type, entity_id]
        generation_clause = ""
        if generation_id is not None:
            generation_clause = "AND ms.generation_id = ?"
            parameters.append(generation_id)
        row = connection.execute(
            f"""SELECT ms.*, vg.identifier AS version_group_identifier
                FROM mechanic_summaries ms
                LEFT JOIN version_groups vg ON vg.id = ms.version_group_id
                WHERE ms.entity_type = ? AND ms.entity_id = ? {generation_clause}
                ORDER BY ms.generation_id DESC,
                         CASE WHEN ms.version_group_id IS NULL THEN 1 ELSE 0 END,
                         ms.version_group_id DESC
                LIMIT 1""",
            parameters,
        ).fetchone()
        if row is None:
            return None
        return {
            "generation_id": int(row["generation_id"]),
            "version_group": row["version_group_identifier"],
            "summary_zh": row["summary_zh"],
            "parameters": json.loads(row["parameters_json"]),
            "source_id": row["source_id"],
            "source_locator": row["source_locator"],
            "verification_note": row["verification_note"],
        }

    def move_summary(self, query: str, language: str = "zh-hans") -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            entity = self._resolve_entity_one(connection, query, "move", language_id)
            row = connection.execute(
                """SELECT m.*, COALESCE(tn_pref.name, tn_en.name, t.identifier) AS type_name,
                          t.identifier AS type_identifier
                   FROM moves m
                   LEFT JOIN types t ON t.id = m.type_id
                   LEFT JOIN type_names tn_pref ON tn_pref.type_id = t.id AND tn_pref.language_id = ?
                   LEFT JOIN type_names tn_en ON tn_en.type_id = t.id AND tn_en.language_id = 9
                   WHERE m.id = ?""",
                (language_id, entity["entity_id"]),
            ).fetchone()
            flavor = self._flavor_text(
                connection, "move_flavor_text", "move_id", int(row["id"]), language_id
            )
            if flavor:
                short_effect, effect_language, description_version, description_source = flavor
                effect = short_effect
                description_kind = "official_game_text"
            else:
                encyclopedia = self._entity_description(
                    connection, "move", int(row["id"]), language_id
                )
                if encyclopedia:
                    short_effect, effect_language, description_source, source_locator = encyclopedia
                    effect = short_effect
                    description_version = None
                    description_kind = "encyclopedia_summary"
                else:
                    short_effect, effect, effect_language = self._effect_text(
                        connection,
                        "move_effect_prose",
                        "move_effect_id",
                        row["effect_id"],
                        language_id,
                        row["effect_chance"],
                    )
                    description_version = None
                    description_source = "pokeapi-csv"
                    description_kind = "mechanical_prose_fallback"
                    source_locator = None
            damage_classes = {1: "status", 2: "physical", 3: "special"}
            mechanic_categories = [
                {
                    "identifier": category["identifier"],
                    "source_flag": category["source_flag_identifier"],
                    "label": category["label_zh"],
                    "description": category["description_zh"],
                    "ruleset_scope": category["ruleset_scope"],
                    "source_id": category["source_id"],
                    "source_locator": category["source_locator"],
                }
                for category in connection.execute(
                    """SELECT mc.identifier, mc.source_flag_identifier, mc.label_zh,
                              mc.description_zh, mm.ruleset_scope, mm.source_id,
                              mm.source_locator
                       FROM move_mechanic_memberships mm
                       JOIN move_mechanic_categories mc
                         ON mc.identifier = mm.category_identifier
                       WHERE mm.move_id = ?
                       ORDER BY mc.label_zh, mc.identifier""",
                    (int(row["id"]),),
                )
            ]
            mechanic_coverage = connection.execute(
                """SELECT ruleset_scope, source_id, source_locator
                   FROM move_mechanic_coverage WHERE move_id = ?""",
                (int(row["id"]),),
            ).fetchone()
            return {
                "entity_type": "move",
                "id": int(row["id"]),
                "identifier": row["identifier"],
                "name": self._entity_label(connection, "move", int(row["id"]), language_id),
                "generation_id": row["generation_id"],
                "type": {"identifier": row["type_identifier"], "name": row["type_name"]},
                "power": row["power"],
                "pp": row["pp"],
                "accuracy": row["accuracy"],
                "priority": row["priority"],
                "damage_class": damage_classes.get(row["damage_class_id"], "unknown"),
                "mechanic_categories": mechanic_categories,
                "mechanic_categories_complete": mechanic_coverage is not None,
                "mechanic_category_ruleset_scope": (
                    mechanic_coverage["ruleset_scope"] if mechanic_coverage else None
                ),
                "short_effect": short_effect,
                "effect": effect,
                "effect_language": effect_language,
                "description_kind": description_kind,
                "description_version_group": description_version,
                "description_source_id": description_source,
                "description_source_locator": None if flavor else source_locator,
                "source_id": "pokeapi-csv",
                "mechanic_category_source_id": (
                    mechanic_coverage["source_id"] if mechanic_coverage else None
                ),
            }

    def ability_summary(self, query: str, language: str = "zh-hans") -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            entity = self._resolve_entity_one(connection, query, "ability", language_id)
            row = connection.execute(
                "SELECT * FROM abilities WHERE id = ?", (entity["entity_id"],)
            ).fetchone()
            flavor = self._flavor_text(
                connection, "ability_flavor_text", "ability_id", int(row["id"]), language_id
            )
            if flavor:
                short_effect, effect_language, description_version, description_source = flavor
                effect = short_effect
                description_kind = "official_game_text"
            else:
                encyclopedia = self._entity_description(
                    connection, "ability", int(row["id"]), language_id
                )
                if encyclopedia:
                    short_effect, effect_language, description_source, source_locator = encyclopedia
                    effect = short_effect
                    description_version = None
                    description_kind = "encyclopedia_summary"
                else:
                    short_effect, effect, effect_language = self._effect_text(
                        connection, "ability_prose", "ability_id", int(row["id"]), language_id
                    )
                    description_version = None
                    description_source = "pokeapi-csv"
                    description_kind = "mechanical_prose_fallback"
                    source_locator = None
            owners = connection.execute(
                """SELECT DISTINCT s.id, s.identifier,
                          COALESCE(sn_pref.name, sn_en.name, s.identifier) AS name,
                          pa.is_hidden
                   FROM pokemon_abilities pa
                   JOIN pokemon_variants p ON p.id = pa.pokemon_id
                   JOIN species s ON s.id = p.species_id
                   LEFT JOIN species_names sn_pref ON sn_pref.species_id = s.id AND sn_pref.language_id = ?
                   LEFT JOIN species_names sn_en ON sn_en.species_id = s.id AND sn_en.language_id = 9
                   WHERE pa.ability_id = ?
                   ORDER BY s.id""",
                (language_id, row["id"]),
            ).fetchall()
            return {
                "entity_type": "ability",
                "id": int(row["id"]),
                "identifier": row["identifier"],
                "name": self._entity_label(connection, "ability", int(row["id"]), language_id),
                "generation_id": row["generation_id"],
                "main_series": bool(row["is_main_series"]),
                "short_effect": short_effect,
                "effect": effect,
                "effect_language": effect_language,
                "description_kind": description_kind,
                "description_version_group": description_version,
                "description_source_id": description_source,
                "description_source_locator": None if flavor else source_locator,
                "mechanics": self._mechanics(connection, "ability", int(row["id"])),
                "pilot_species": [dict(owner) for owner in owners],
                "source_id": "pokeapi-csv",
            }

    def item_summary(self, query: str, language: str = "zh-hans") -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            entity = self._resolve_entity_one(connection, query, "item", language_id)
            row = connection.execute(
                """SELECT i.*, c.identifier AS category_identifier, p.identifier AS pocket_identifier,
                          COALESCE(cn_pref.name, cn_en.name, c.identifier) AS category_name,
                          COALESCE(pn_pref.name, pn_en.name, p.identifier) AS pocket_name
                   FROM items i
                   JOIN item_categories c ON c.id = i.category_id
                   JOIN item_pockets p ON p.id = c.pocket_id
                   LEFT JOIN item_category_names cn_pref
                     ON cn_pref.item_category_id = c.id AND cn_pref.language_id = ?
                   LEFT JOIN item_category_names cn_en
                     ON cn_en.item_category_id = c.id AND cn_en.language_id = 9
                   LEFT JOIN item_pocket_names pn_pref
                     ON pn_pref.item_pocket_id = p.id AND pn_pref.language_id = ?
                   LEFT JOIN item_pocket_names pn_en
                     ON pn_en.item_pocket_id = p.id AND pn_en.language_id = 9
                   WHERE i.id = ?""",
                (language_id, language_id, entity["entity_id"]),
            ).fetchone()
            flavor = self._flavor_text(
                connection, "item_flavor_text", "item_id", int(row["id"]), language_id
            )
            if flavor:
                short_effect, effect_language, description_version, description_source = flavor
                effect = short_effect
                description_kind = "official_game_text"
            else:
                encyclopedia = self._entity_description(
                    connection, "item", int(row["id"]), language_id
                )
                if encyclopedia:
                    short_effect, effect_language, description_source, source_locator = encyclopedia
                    effect = short_effect
                    description_version = None
                    description_kind = "encyclopedia_summary"
                else:
                    short_effect, effect, effect_language = self._effect_text(
                        connection, "item_prose", "item_id", int(row["id"]), language_id
                    )
                    description_version = None
                    description_source = "pokeapi-csv"
                    description_kind = "mechanical_prose_fallback"
                    source_locator = None
            return {
                "entity_type": "item",
                "id": int(row["id"]),
                "identifier": row["identifier"],
                "name": self._entity_label(connection, "item", int(row["id"]), language_id),
                "category": {"identifier": row["category_identifier"], "name": row["category_name"]},
                "pocket": {"identifier": row["pocket_identifier"], "name": row["pocket_name"]},
                "cost": row["cost"],
                "fling_power": row["fling_power"],
                "short_effect": short_effect,
                "effect": effect,
                "effect_language": effect_language,
                "description_kind": description_kind,
                "description_version_group": description_version,
                "description_source_id": description_source,
                "description_source_locator": None if flavor else source_locator,
                "mechanics": self._mechanics(connection, "item", int(row["id"])),
                "source_id": "pokeapi-csv",
            }

    def find_entities_in_text(
        self, text: str, entity_types: tuple[str, ...], language: str = "zh-hans"
    ) -> list[dict[str, Any]]:
        normalized = normalize_alias(text)
        if not normalized or not entity_types:
            return []
        placeholders = ",".join("?" for _ in entity_types)
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            rows = connection.execute(
                f"""SELECT DISTINCT e.entity_type, e.entity_id, e.identifier, e.detail_ready,
                           a.alias_normalized
                    FROM entity_aliases a
                    JOIN entities e
                      ON e.entity_type = a.entity_type AND e.entity_id = a.entity_id
                    WHERE e.entity_type IN ({placeholders})
                      AND length(a.alias_normalized) >= 2
                      AND a.alias_normalized NOT GLOB '[0-9]*'
                      AND instr(?, a.alias_normalized) > 0
                    ORDER BY length(a.alias_normalized) DESC, e.entity_type, e.entity_id""",
                [*entity_types, normalized],
            ).fetchall()
            seen: set[tuple[str, int]] = set()
            result: list[dict[str, Any]] = []
            for row in rows:
                key = (row["entity_type"], int(row["entity_id"]))
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    {
                        "entity_type": row["entity_type"],
                        "entity_id": int(row["entity_id"]),
                        "identifier": row["identifier"],
                        "name": self._entity_label(
                            connection, row["entity_type"], int(row["entity_id"]), language_id
                        ),
                        "detail_ready": bool(row["detail_ready"]),
                        "matched_length": len(row["alias_normalized"]),
                    }
                )
            return result

    def search_passages(
        self,
        query: str,
        linked_entities: list[dict[str, Any]] | None = None,
        generation_id: int | None = None,
        version_group: str | None = "scarlet-violet",
        edition_id: str = "mainline",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve section-level evidence with FTS plus entity-linked recall.

        FTS supplies broad Chinese candidates; explicit entity links make recall
        deterministic for paraphrased questions.  A lightweight local reranker
        keeps this API useful before an embedding model or LLM is connected.
        """
        query = query.strip()
        if not query or limit <= 0:
            return []
        linked_entities = linked_entities or []
        entity_keys = {
            (str(row["entity_type"]), int(row["entity_id"]))
            for row in linked_entities
            if "entity_type" in row and "entity_id" in row
        }
        with closing(self._connect()) as connection:
            version_group_id: int | None = None
            if version_group is not None:
                version = connection.execute(
                    "SELECT id, generation_id FROM version_groups WHERE identifier = ?",
                    (version_group,),
                ).fetchone()
                if version is None:
                    raise ValueError(f"Unknown version group: {version_group}")
                version_group_id = int(version["id"])
                version_generation = int(version["generation_id"])
                if generation_id is None:
                    generation_id = version_generation
                elif generation_id != version_generation:
                    raise ValueError(
                        f"Generation {generation_id} does not match version group {version_group}"
                    )
            if generation_id is None:
                generation_id = 9

            scope_sql = """kp.edition_id = ?
                AND (kp.generation_from IS NULL OR kp.generation_from <= ?)
                AND (kp.generation_to IS NULL OR kp.generation_to >= ?)
                AND (kp.version_group_id IS NULL OR kp.version_group_id = ?)"""
            scope_parameters: list[object] = [
                edition_id,
                generation_id,
                generation_id,
                version_group_id,
            ]
            candidate_ids: set[str] = set()
            fts_order: dict[str, int] = {}
            match_query = _fts_match_query(query)
            if match_query:
                fts_rows = connection.execute(
                    f"""SELECT kp.passage_id
                         FROM knowledge_passages_fts
                         JOIN knowledge_passages kp
                           ON kp.passage_id = knowledge_passages_fts.passage_id
                         WHERE knowledge_passages_fts MATCH ? AND {scope_sql}
                         ORDER BY bm25(knowledge_passages_fts)
                         LIMIT 80""",
                    [match_query, *scope_parameters],
                ).fetchall()
                for position, row in enumerate(fts_rows):
                    passage_id = str(row["passage_id"])
                    candidate_ids.add(passage_id)
                    fts_order[passage_id] = position

            if entity_keys:
                entity_predicates = " OR ".join(
                    "(pe.entity_type = ? AND pe.entity_id = ?)" for _ in entity_keys
                )
                entity_parameters: list[object] = []
                for entity_type, entity_id in sorted(entity_keys):
                    entity_parameters.extend((entity_type, entity_id))
                linked_rows = connection.execute(
                    f"""SELECT DISTINCT kp.passage_id
                         FROM passage_entities pe
                         JOIN knowledge_passages kp ON kp.passage_id = pe.passage_id
                         WHERE ({entity_predicates}) AND {scope_sql}
                         LIMIT 80""",
                    [*entity_parameters, *scope_parameters],
                ).fetchall()
                candidate_ids.update(str(row["passage_id"]) for row in linked_rows)

            if not candidate_ids:
                return []
            placeholders = ",".join("?" for _ in candidate_ids)
            rows = connection.execute(
                f"""SELECT kp.*, kd.title AS document_title, kd.source_name,
                            kd.source_url, kd.source_kind, kd.license_identifier,
                            kd.retrieved_at, kd.source_revision,
                            vg.identifier AS version_group_identifier
                     FROM knowledge_passages kp
                     JOIN knowledge_documents kd ON kd.document_id = kp.document_id
                     LEFT JOIN version_groups vg ON vg.id = kp.version_group_id
                     WHERE kp.passage_id IN ({placeholders})""",
                sorted(candidate_ids),
            ).fetchall()

            query_bigrams = _character_ngrams(query, 2)
            query_trigrams = _character_ngrams(query, 3)
            intent_terms = {
                term
                for term in (
                    "不会", "不能", "失败", "排除", "例外", "随机", "选择",
                    "持续", "回合", "互动", "复制", "发动", "历史", "变化",
                )
                if term in query
            }
            ranked: list[dict[str, Any]] = []
            for row in rows:
                passage_id = str(row["passage_id"])
                searchable = f"{row['document_title']} {row['heading']} {row['section_path']} {row['content']}"
                passage_bigrams = _character_ngrams(searchable, 2)
                passage_trigrams = _character_ngrams(searchable, 3)
                score = 0.0
                if query_bigrams:
                    score += 8.0 * len(query_bigrams & passage_bigrams) / len(query_bigrams)
                if query_trigrams:
                    score += 12.0 * len(query_trigrams & passage_trigrams) / len(query_trigrams)
                score += 2.5 * sum(term in searchable for term in intent_terms)
                list_intent = any(term in query for term in ("哪些", "有哪些", "列出", "名单"))
                list_section = any(
                    term in f"{row['heading']} {row['section_path']}"
                    for term in ("不会使出的招式", "失败条件", "排除目录", "名单")
                )
                if list_intent and list_section:
                    score += 10.0
                passage_links = connection.execute(
                    """SELECT pe.entity_type, pe.entity_id, pe.relation_kind, e.identifier
                       FROM passage_entities pe
                       JOIN entities e
                         ON e.entity_type = pe.entity_type AND e.entity_id = pe.entity_id
                       WHERE pe.passage_id = ?
                       ORDER BY pe.relation_kind, pe.entity_type, pe.entity_id""",
                    (passage_id,),
                ).fetchall()
                matched_links = [
                    link for link in passage_links
                    if (str(link["entity_type"]), int(link["entity_id"])) in entity_keys
                ]
                if matched_links:
                    score += 6.0
                    if any(link["relation_kind"] == "subject" for link in matched_links):
                        score += 3.0
                if passage_id in fts_order:
                    score += max(0.2, 2.0 - fts_order[passage_id] * 0.03)
                ranked.append(
                    {
                        "passage_id": passage_id,
                        "document_id": row["document_id"],
                        "document_title": row["document_title"],
                        "section_path": row["section_path"],
                        "heading": row["heading"],
                        "content": row["content"],
                        "generation_from": row["generation_from"],
                        "generation_to": row["generation_to"],
                        "version_group": row["version_group_identifier"],
                        "edition_id": row["edition_id"],
                        "score": round(score, 4),
                        "source": {
                            "name": row["source_name"],
                            "url": row["source_url"],
                            "kind": row["source_kind"],
                            "license": row["license_identifier"],
                            "retrieved_at": row["retrieved_at"],
                            "revision": row["source_revision"],
                        },
                        "entities": [dict(link) for link in passage_links],
                        "retrieval": {
                            "fts_match": passage_id in fts_order,
                            "entity_link_match": bool(matched_links),
                        },
                    }
                )
            ranked.sort(key=lambda row: (-row["score"], row["passage_id"]))
            return ranked[:limit]

    def can_learn(
        self,
        species_query: str,
        move_query: str,
        version_group: str | None = None,
        language: str = "zh-hans",
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            species = self._resolve_one(connection, species_query, language_id)
            move = self._resolve_entity_one(connection, move_query, "move", language_id)
            pokemon = connection.execute(
                "SELECT * FROM pokemon_variants WHERE species_id = ? AND is_default = 1",
                (species["id"],),
            ).fetchone()
            if pokemon is None:
                raise EntityNotFoundError(f"Default variant not found for {species_query}")
            version, version_selection, available_versions = self._select_learnset_version(
                connection, int(pokemon["id"]), version_group
            )
            version_is_available = any(
                row["identifier"] == version["identifier"] for row in available_versions
            )
            rows = connection.execute(
                """SELECT DISTINCT mm.identifier AS method, l.level
                   FROM learnsets l
                   JOIN move_methods mm ON mm.id = l.method_id
                   WHERE l.pokemon_id = ? AND l.move_id = ? AND l.version_group_id = ?
                   ORDER BY mm.identifier, l.level""",
                (pokemon["id"], move["entity_id"], version["id"]),
            ).fetchall()
            return {
                "species_name": self._species_label(connection, int(species["id"]), language_id),
                "move_name": self._entity_label(connection, "move", int(move["entity_id"]), language_id),
                "requested_version_group": version_group,
                "version_group": version["identifier"],
                "version_selection": version_selection,
                "latest_available_version_group": available_versions[0]["identifier"],
                "available_version_groups": available_versions,
                "status": "available" if version_is_available else "unavailable_in_version",
                "status_zh": (
                    "已在该版本学习面中完成判定。"
                    if version_is_available
                    else "该指定版本没有此宝可梦的学习面记录，无法据此判定能否学习。"
                ),
                "can_learn": bool(rows) if version_is_available else None,
                "methods": [dict(row) for row in rows],
                "source_id": "pokeapi-csv",
            }

    def move_learners(
        self, move_query: str, version_group: str, language: str = "zh-hans"
    ) -> dict[str, Any]:
        """Return all species/form relationships for a move in one version group."""
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            move = self._resolve_entity_one(connection, move_query, "move", language_id)
            version = connection.execute(
                "SELECT id FROM version_groups WHERE identifier = ?", (version_group,)
            ).fetchone()
            if version is None:
                raise ValueError(f"Unknown version group: {version_group}")
            rows = connection.execute(
                """SELECT DISTINCT s.id AS species_id, s.identifier AS species_identifier,
                          COALESCE(sn_pref.name, sn_en.name, s.identifier) AS species_name,
                          p.identifier AS pokemon_identifier, p.is_default,
                          mm.identifier AS method_identifier
                   FROM learnsets l
                   JOIN pokemon_variants p ON p.id = l.pokemon_id
                   JOIN species s ON s.id = p.species_id
                   JOIN move_methods mm ON mm.id = l.method_id
                   LEFT JOIN species_names sn_pref
                     ON sn_pref.species_id = s.id AND sn_pref.language_id = ?
                   LEFT JOIN species_names sn_en
                     ON sn_en.species_id = s.id AND sn_en.language_id = 9
                   WHERE l.move_id = ? AND l.version_group_id = ?
                   ORDER BY s.id, p.is_default DESC, p.id, mm.identifier""",
                (language_id, move["entity_id"], version["id"]),
            ).fetchall()
            return {
                "edition_id": "mainline",
                "move_id": int(move["entity_id"]),
                "move_identifier": move["identifier"],
                "move_name": self._entity_label(
                    connection, "move", int(move["entity_id"]), language_id
                ),
                "version_group": version_group,
                "learners": [dict(row) for row in rows],
                "source_id": "pokeapi-csv",
            }

    def compare_species(
        self,
        queries: list[str],
        fields: list[str],
        language: str = "zh-hans",
    ) -> dict[str, Any]:
        if len(queries) < 2:
            raise ValueError("compare_species requires at least two species")
        allowed = {
            "hp", "attack", "defense", "special-attack", "special-defense", "speed",
            "base-stat-total", "height", "weight", "capture-rate", "base-happiness",
        }
        invalid = [field for field in fields if field not in allowed]
        if invalid:
            raise ValueError(f"Unsupported comparison fields: {invalid}")
        entries: list[dict[str, Any]] = []
        for query in queries:
            summary = self.species_summary(query, language)
            species = summary["species"]
            variant = next(row for row in summary["variants"] if row["default"])
            values: dict[str, int | float | None] = {}
            for field in fields:
                if field in variant["stats"]:
                    values[field] = variant["stats"][field]
                elif field == "base-stat-total":
                    values[field] = variant["base_stat_total"]
                elif field == "height":
                    values[field] = variant["height_m"]
                elif field == "weight":
                    values[field] = variant["weight_kg"]
                elif field == "capture-rate":
                    values[field] = species["capture_rate"]
                elif field == "base-happiness":
                    values[field] = species["base_happiness"]
            entries.append(
                {
                    "species_id": species["species_id"],
                    "identifier": species["identifier"],
                    "name": species["name"],
                    "pokemon_identifier": variant["identifier"],
                    "values": values,
                }
            )
        comparisons: dict[str, Any] = {}
        for field in fields:
            comparable = [entry for entry in entries if entry["values"].get(field) is not None]
            if not comparable:
                comparisons[field] = {"leaders": [], "maximum": None, "spread": None}
                continue
            maximum = max(entry["values"][field] for entry in comparable)
            minimum = min(entry["values"][field] for entry in comparable)
            comparisons[field] = {
                "leaders": [entry["name"] for entry in comparable if entry["values"][field] == maximum],
                "maximum": maximum,
                "minimum": minimum,
                "spread": maximum - minimum,
            }
        return {"form_scope": "default", "species": entries, "comparisons": comparisons}

    def filter_species(
        self,
        stat_filters: dict[str, dict[str, int]] | None = None,
        generation_ids: list[int] | None = None,
        type_identifiers: list[str] | None = None,
        ability_identifiers: list[str] | None = None,
        tag_keys: list[str] | None = None,
        ordinary_only: bool = False,
        sort_by: str = "national-number",
        descending: bool = False,
        limit: int = 200,
        language: str = "zh-hans",
    ) -> dict[str, Any]:
        """Filter default forms using exact SQL predicates, suitable for tool calling."""
        stat_filters = stat_filters or {}
        generation_ids = generation_ids or []
        type_identifiers = type_identifiers or []
        ability_identifiers = ability_identifiers or []
        tag_keys = tag_keys or []
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        stat_columns = {
            "hp": "hp", "attack": "attack", "defense": "defense",
            "special-attack": "special_attack", "special-defense": "special_defense",
            "speed": "speed", "base-stat-total": "base_stat_total",
        }
        operators = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}
        for stat, predicates in stat_filters.items():
            if stat not in stat_columns:
                raise ValueError(f"Unsupported stat filter: {stat}")
            if any(operator not in operators for operator in predicates):
                raise ValueError(f"Unsupported operator for {stat}: {list(predicates)}")
        sort_columns = {"national-number": "species_id", **stat_columns}
        if sort_by not in sort_columns:
            raise ValueError(f"Unsupported sort field: {sort_by}")

        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            where = ["p.is_default = 1"]
            parameters: list[object] = []
            if generation_ids:
                where.append(f"s.generation_id IN ({','.join('?' for _ in generation_ids)})")
                parameters.extend(generation_ids)
            if ordinary_only:
                where.extend(("s.is_legendary = 0", "s.is_mythical = 0"))
            for type_identifier in type_identifiers:
                where.append(
                    """EXISTS (SELECT 1 FROM pokemon_types pt JOIN types t ON t.id = pt.type_id
                               WHERE pt.pokemon_id = p.id AND t.identifier = ?)"""
                )
                parameters.append(type_identifier)
            for ability_identifier in ability_identifiers:
                where.append(
                    """EXISTS (SELECT 1 FROM pokemon_abilities pa JOIN abilities a ON a.id = pa.ability_id
                               WHERE pa.pokemon_id = p.id AND a.identifier = ?)"""
                )
                parameters.append(ability_identifier)
            for tag_key in tag_keys:
                where.append(
                    """EXISTS (SELECT 1 FROM entity_tags et JOIN tags tag ON tag.tag_id = et.tag_id
                               WHERE et.entity_type = 'species' AND et.entity_id = s.id
                                 AND tag.tag_key = ?)"""
                )
                parameters.append(tag_key)

            stat_select = ",\n".join(
                f"MAX(CASE WHEN st.identifier = '{identifier}' THEN ps.base_stat END) AS {column}"
                for identifier, column in stat_columns.items()
                if identifier != "base-stat-total"
            )
            inner_sql = f"""SELECT s.id AS species_id, s.identifier, s.generation_id,
                         p.id AS pokemon_id, p.identifier AS pokemon_identifier,
                         {stat_select}, SUM(ps.base_stat) AS base_stat_total
                  FROM species s
                  JOIN pokemon_variants p ON p.species_id = s.id
                  JOIN pokemon_stats ps ON ps.pokemon_id = p.id
                  JOIN stats st ON st.id = ps.stat_id
                  WHERE {' AND '.join(where)}
                  GROUP BY s.id, p.id"""
            outer_where: list[str] = []
            for stat, predicates in stat_filters.items():
                for operator, value in predicates.items():
                    outer_where.append(f"{stat_columns[stat]} {operators[operator]} ?")
                    parameters.append(value)
            outer_clause = "" if not outer_where else "WHERE " + " AND ".join(outer_where)
            order = "DESC" if descending else "ASC"
            rows = connection.execute(
                f"""SELECT * FROM ({inner_sql}) filtered
                     {outer_clause}
                     ORDER BY {sort_columns[sort_by]} {order}, species_id ASC
                     LIMIT ?""",
                [*parameters, limit],
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                results.append(
                    {
                        "species_id": int(row["species_id"]),
                        "identifier": row["identifier"],
                        "name": self._species_label(connection, int(row["species_id"]), language_id),
                        "pokemon_identifier": row["pokemon_identifier"],
                        "generation_id": int(row["generation_id"]),
                        "stats": {key: int(row[column]) for key, column in stat_columns.items() if key != "base-stat-total"},
                        "base_stat_total": int(row["base_stat_total"]),
                    }
                )
            return {
                "form_scope": "default",
                "ordinary_definition": "is_legendary = 0 and is_mythical = 0" if ordinary_only else None,
                "count": len(results),
                "truncated": len(results) == limit,
                "results": results,
            }

    def get_evolution_chain(self, query: str, language: str = "zh-hans") -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            focus = self._resolve_one(connection, query, language_id)
            edges = connection.execute(
                """SELECT e.*, et.identifier AS trigger_identifier
                   FROM evolutions e
                   JOIN evolution_triggers et ON et.id = e.trigger_id
                   ORDER BY e.id"""
            ).fetchall()
            connected = {int(focus["id"])}
            changed = True
            while changed:
                changed = False
                for edge in edges:
                    source = edge["from_species_id"]
                    target = int(edge["evolved_species_id"])
                    if source is None:
                        continue
                    source = int(source)
                    if source in connected or target in connected:
                        before = len(connected)
                        connected.update((source, target))
                        changed = changed or len(connected) != before
            chain_edges = []
            for edge in edges:
                source = edge["from_species_id"]
                target = int(edge["evolved_species_id"])
                if source is None or int(source) not in connected or target not in connected:
                    continue
                version_group = None
                if edge["version_group_id"] is not None:
                    version_group = connection.execute(
                        "SELECT identifier FROM version_groups WHERE id = ?", (edge["version_group_id"],)
                    ).fetchone()[0]
                chain_edges.append(
                    {
                        "from_species_id": int(source),
                        "from_name": self._species_label(connection, int(source), language_id),
                        "to_species_id": target,
                        "to_name": self._species_label(connection, target, language_id),
                        "trigger": edge["trigger_identifier"],
                        "version_group": version_group,
                        "conditions": json.loads(edge["conditions_json"]),
                    }
                )
            return {
                "focus": {
                    "species_id": int(focus["id"]),
                    "identifier": focus["identifier"],
                    "name": self._species_label(connection, int(focus["id"]), language_id),
                },
                "species": [
                    {
                        "species_id": species_id,
                        "name": self._species_label(connection, species_id, language_id),
                    }
                    for species_id in sorted(connected)
                ],
                "evolutions": chain_edges,
                "source_id": "pokeapi-csv",
            }

    def get_species_relations(
        self, query: str, version_group: str | None = None, language: str = "zh-hans"
    ) -> dict[str, Any]:
        summary = self.species_summary(query, language)
        result: dict[str, Any] = {
            "species": summary["species"],
            "variants": summary["variants"],
            "egg_groups": summary["egg_groups"],
            "pokedex_memberships": summary["pokedex_memberships"],
            "evolution_chain": self.get_evolution_chain(query, language),
            "tags": self.get_entity_tags(query, "species", language),
        }
        result["learnset"] = self.learnset(query, version_group, language)
        if version_group:
            result["held_items"] = self.held_items(query, version_group, language)
        return result

    def get_entity_tags(
        self, query: str, entity_type: str, language: str = "zh-hans"
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            entity = self._resolve_entity_one(connection, query, entity_type, language_id)
            rows = connection.execute(
                """SELECT t.tag_key, t.category, t.label_zh, t.description_zh,
                          et.assignment_method, et.confidence, et.evidence_json
                   FROM entity_tags et JOIN tags t ON t.tag_id = et.tag_id
                   WHERE et.entity_type = ? AND et.entity_id = ?
                   ORDER BY t.category, t.label_zh""",
                (entity_type, entity["entity_id"]),
            ).fetchall()
            return {
                "entity_type": entity_type,
                "entity_id": int(entity["entity_id"]),
                "identifier": entity["identifier"],
                "name": self._entity_label(connection, entity_type, int(entity["entity_id"]), language_id),
                "tags": [
                    {
                        "key": row["tag_key"], "category": row["category"],
                        "label": row["label_zh"], "description": row["description_zh"],
                        "assignment_method": row["assignment_method"],
                        "confidence": float(row["confidence"]),
                        "evidence": json.loads(row["evidence_json"]),
                    }
                    for row in rows
                ],
            }

    def search_by_tags(
        self,
        tag_queries: list[str],
        entity_type: str | None = None,
        match_all: bool = True,
        limit: int = 100,
        language: str = "zh-hans",
    ) -> dict[str, Any]:
        if not tag_queries:
            return {"matched_tags": [], "count": 0, "results": []}
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            matched_tags = []
            for query in tag_queries:
                normalized = normalize_alias(query)
                candidates = []
                for row in connection.execute("SELECT * FROM tags"):
                    label = normalize_alias(str(row["label_zh"]))
                    key = normalize_alias(str(row["tag_key"]))
                    if normalized == label or normalized == key:
                        score = 100.0
                    else:
                        query_grams = _character_ngrams(normalized, 2)
                        target_grams = _character_ngrams(label, 2)
                        score = 0.0 if not query_grams else 10.0 * len(query_grams & target_grams) / len(query_grams)
                        if normalized in label:
                            score += 20.0
                    if score > 0:
                        candidates.append((score, row))
                if not candidates:
                    raise ValueError(f"Tag not found: {query}")
                candidates.sort(key=lambda value: (-value[0], int(value[1]["tag_id"])))
                selected = candidates[0][1]
                matched_tags.append(
                    {
                        "query": query,
                        "tag_id": int(selected["tag_id"]),
                        "key": selected["tag_key"],
                        "category": selected["category"],
                        "label": selected["label_zh"],
                    }
                )
            tag_ids = [row["tag_id"] for row in matched_tags]
            placeholders = ",".join("?" for _ in tag_ids)
            type_clause = "" if entity_type is None else "AND et.entity_type = ?"
            parameters: list[object] = [*tag_ids]
            if entity_type is not None:
                parameters.append(entity_type)
            having = "HAVING COUNT(DISTINCT et.tag_id) = ?" if match_all else ""
            if match_all:
                parameters.append(len(tag_ids))
            parameters.append(limit)
            rows = connection.execute(
                f"""SELECT et.entity_type, et.entity_id, e.identifier
                     FROM entity_tags et
                     JOIN entities e ON e.entity_type = et.entity_type AND e.entity_id = et.entity_id
                     WHERE et.tag_id IN ({placeholders}) {type_clause}
                     GROUP BY et.entity_type, et.entity_id
                     {having}
                     ORDER BY et.entity_type, et.entity_id LIMIT ?""",
                parameters,
            ).fetchall()
            results = [
                {
                    "entity_type": row["entity_type"], "entity_id": int(row["entity_id"]),
                    "identifier": row["identifier"],
                    "name": self._entity_label(connection, row["entity_type"], int(row["entity_id"]), language_id),
                }
                for row in rows
            ]
            return {"matched_tags": matched_tags, "match_all": match_all, "count": len(results), "results": results}

    def match_tags_in_text(
        self, text: str, entity_type: str = "species", limit: int = 8
    ) -> list[dict[str, Any]]:
        """Extract explicit tag phrases from a natural-language query."""
        normalized = normalize_alias(text)
        if not normalized:
            return []
        allowed_categories = {
            "ability", "appearance", "base_stat", "classification", "egg_group",
            "effort_yield", "evolution", "form", "generation", "growth", "habitat",
            "item_category", "item_pocket", "move_class", "power", "priority",
            "regional_pokedex", "scope", "status", "type", "effect", "move_mechanic",
        }
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT DISTINCT t.tag_id, t.tag_key, t.category, t.label_zh
                   FROM tags t JOIN entity_tags et ON et.tag_id = t.tag_id
                   WHERE et.entity_type = ? ORDER BY length(t.label_zh) DESC, t.tag_id""",
                (entity_type,),
            ).fetchall()
            candidates: list[tuple[int, sqlite3.Row, str]] = []
            for row in rows:
                if row["category"] not in allowed_categories:
                    continue
                label = normalize_alias(str(row["label_zh"]))
                cores = {label}
                for suffix in ("的宝可梦", "宝可梦", "的招式", "招式", "的特性", "特性", "类道具", "的道具", "道具"):
                    if label.endswith(normalize_alias(suffix)):
                        cores.add(label[: -len(normalize_alias(suffix))])
                cores = {core for core in cores if len(core) >= 2 and core not in {"属性", "拥有", "主系列"}}
                matched = max((core for core in cores if core in normalized), key=len, default="")
                if matched:
                    candidates.append((len(matched), row, matched))
            candidates.sort(key=lambda value: (-value[0], int(value[1]["tag_id"])))
            selected: list[dict[str, Any]] = []
            selected_cores: list[str] = []
            for _, row, core in candidates:
                if any(core in existing or existing in core for existing in selected_cores):
                    continue
                selected.append(
                    {"tag_id": int(row["tag_id"]), "key": row["tag_key"], "category": row["category"], "label": row["label_zh"], "matched_text": core}
                )
                selected_cores.append(core)
                if len(selected) >= limit:
                    break
            return selected

    def held_items(
        self, species_query: str, version_group: str, language: str = "zh-hans"
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            language_id = self._language_id(connection, language)
            species = self._resolve_one(connection, species_query, language_id)
            version = connection.execute(
                "SELECT id FROM version_groups WHERE identifier = ?", (version_group,)
            ).fetchone()
            if version is None:
                raise ValueError(f"Unknown version group: {version_group}")
            rows = connection.execute(
                """SELECT DISTINCT i.id AS item_id, i.identifier AS item_identifier,
                          COALESCE(n_pref.name, n_en.name, i.identifier) AS item_name,
                          gv.identifier AS game_version, pi.rarity
                   FROM pokemon_variants p
                   JOIN pokemon_items pi ON pi.pokemon_id = p.id
                   JOIN game_versions gv ON gv.id = pi.version_id
                   JOIN items i ON i.id = pi.item_id
                   LEFT JOIN item_names n_pref ON n_pref.item_id = i.id AND n_pref.language_id = ?
                   LEFT JOIN item_names n_en ON n_en.item_id = i.id AND n_en.language_id = 9
                   WHERE p.species_id = ? AND p.is_default = 1 AND gv.version_group_id = ?
                   ORDER BY gv.id, pi.rarity DESC, i.id""",
                (language_id, species["id"], version["id"]),
            ).fetchall()
            return {
                "species_name": self._species_label(connection, int(species["id"]), language_id),
                "version_group": version_group,
                "items": [dict(row) for row in rows],
                "source_id": "pokeapi-csv",
            }
