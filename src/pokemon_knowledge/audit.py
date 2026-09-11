from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .database import connect_readonly


def audit_pilot(settings: Settings | None = None, write_report: bool = True) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    issues: list[dict[str, Any]] = []
    with closing(connect_readonly(settings.database_path)) as connection:
        pilot_ids = [int(row[0]) for row in connection.execute("SELECT species_id FROM pilot_species ORDER BY species_id")]
        detailed_ids = [
            int(row[0])
            for row in connection.execute(
                """SELECT entity_id FROM entities
                   WHERE entity_type = 'species' AND detail_ready = 1
                   ORDER BY entity_id"""
            )
        ]
        for species_id in detailed_ids:
            identifier = connection.execute("SELECT identifier FROM species WHERE id = ?", (species_id,)).fetchone()[0]
            zh_count = connection.execute(
                """SELECT COUNT(*) FROM species_names sn
                   WHERE species_id = ? AND language_id = 12 AND name <> ''""",
                (species_id,),
            ).fetchone()[0]
            default_rows = connection.execute(
                "SELECT id FROM pokemon_variants WHERE species_id = ? AND is_default = 1",
                (species_id,),
            ).fetchall()
            if zh_count != 1:
                issues.append({"species": identifier, "check": "zh_hans_name", "actual": zh_count})
            if len(default_rows) != 1:
                issues.append({"species": identifier, "check": "default_variant", "actual": len(default_rows)})
                continue
            pokemon_id = int(default_rows[0][0])
            relation_counts = {
                "types": connection.execute("SELECT COUNT(*) FROM pokemon_types WHERE pokemon_id = ?", (pokemon_id,)).fetchone()[0],
                "stats": connection.execute("SELECT COUNT(*) FROM pokemon_stats WHERE pokemon_id = ?", (pokemon_id,)).fetchone()[0],
                "abilities": connection.execute("SELECT COUNT(*) FROM pokemon_abilities WHERE pokemon_id = ?", (pokemon_id,)).fetchone()[0],
                "egg_groups": connection.execute("SELECT COUNT(*) FROM species_egg_groups WHERE species_id = ?", (species_id,)).fetchone()[0],
            }
            if not 1 <= relation_counts["types"] <= 2:
                issues.append({"species": identifier, "check": "type_count", "actual": relation_counts["types"]})
            if relation_counts["stats"] != 6:
                issues.append({"species": identifier, "check": "stat_count", "actual": relation_counts["stats"]})
            if not 1 <= relation_counts["abilities"] <= 3:
                issues.append({"species": identifier, "check": "ability_count", "actual": relation_counts["abilities"]})
            if not 1 <= relation_counts["egg_groups"] <= 2:
                issues.append({"species": identifier, "check": "egg_group_count", "actual": relation_counts["egg_groups"]})

        counts = {
            "species_catalog": connection.execute("SELECT COUNT(*) FROM species").fetchone()[0],
            "detailed_species": len(detailed_ids),
            "species_with_zh_name": connection.execute(
                "SELECT COUNT(DISTINCT species_id) FROM species_names WHERE language_id = 12 AND name <> ''"
            ).fetchone()[0],
            "species_with_zh_flavor_text": connection.execute(
                "SELECT COUNT(DISTINCT species_id) FROM species_flavor_text WHERE language_id = 12"
            ).fetchone()[0],
            "species_with_any_flavor_text": connection.execute(
                "SELECT COUNT(DISTINCT species_id) FROM species_flavor_text"
            ).fetchone()[0],
            "species_with_move_relations": connection.execute(
                """SELECT COUNT(DISTINCT s.id)
                   FROM species s
                   JOIN pokemon_variants p ON p.species_id = s.id
                   JOIN learnsets l ON l.pokemon_id = p.id"""
            ).fetchone()[0],
            "species_with_ability_relations": connection.execute(
                """SELECT COUNT(DISTINCT s.id)
                   FROM species s
                   JOIN pokemon_variants p ON p.species_id = s.id
                   JOIN pokemon_abilities pa ON pa.pokemon_id = p.id"""
            ).fetchone()[0],
            "pilot_species": len(pilot_ids),
            "pokemon_variants": connection.execute("SELECT COUNT(*) FROM pokemon_variants").fetchone()[0],
            "forms": connection.execute("SELECT COUNT(*) FROM pokemon_forms").fetchone()[0],
            "localized_species_names": connection.execute("SELECT COUNT(*) FROM species_names").fetchone()[0],
            "entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "entity_aliases": connection.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0],
            "abilities": connection.execute("SELECT COUNT(*) FROM abilities").fetchone()[0],
            "moves": connection.execute("SELECT COUNT(*) FROM moves").fetchone()[0],
            "move_mechanic_categories": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_categories"
            ).fetchone()[0],
            "move_mechanic_memberships": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_memberships"
            ).fetchone()[0],
            "moves_with_mechanic_coverage": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_coverage"
            ).fetchone()[0],
            "moves_with_mechanic_categories": connection.execute(
                "SELECT COUNT(DISTINCT move_id) FROM move_mechanic_memberships"
            ).fetchone()[0],
            "contact_move_memberships": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_memberships WHERE category_identifier = 'contact'"
            ).fetchone()[0],
            "slicing_move_memberships": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_memberships WHERE category_identifier = 'slicing'"
            ).fetchone()[0],
            "wind_move_memberships": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_memberships WHERE category_identifier = 'wind'"
            ).fetchone()[0],
            "ballistics_move_memberships": connection.execute(
                "SELECT COUNT(*) FROM move_mechanic_memberships WHERE category_identifier = 'ballistics'"
            ).fetchone()[0],
            "items": connection.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "moves_with_zh_description": connection.execute(
                "SELECT COUNT(DISTINCT move_id) FROM move_flavor_text WHERE language_id = 12"
            ).fetchone()[0],
            "abilities_with_zh_description": connection.execute(
                "SELECT COUNT(DISTINCT ability_id) FROM ability_flavor_text WHERE language_id = 12"
            ).fetchone()[0],
            "items_with_zh_description": connection.execute(
                "SELECT COUNT(DISTINCT item_id) FROM item_flavor_text WHERE language_id = 12"
            ).fetchone()[0],
            "encyclopedia_zh_descriptions": connection.execute(
                "SELECT COUNT(*) FROM entity_descriptions WHERE language_id = 12"
            ).fetchone()[0],
            "latest_mechanic_summaries": connection.execute(
                "SELECT COUNT(*) FROM mechanic_summaries"
            ).fetchone()[0],
            "knowledge_documents": connection.execute(
                "SELECT COUNT(*) FROM knowledge_documents"
            ).fetchone()[0],
            "knowledge_passages": connection.execute(
                "SELECT COUNT(*) FROM knowledge_passages"
            ).fetchone()[0],
            "passage_entity_links": connection.execute(
                "SELECT COUNT(*) FROM passage_entities"
            ).fetchone()[0],
            "knowledge_fts_rows": connection.execute(
                "SELECT COUNT(*) FROM knowledge_passages_fts"
            ).fetchone()[0],
            "tags": connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            "entity_tags": connection.execute("SELECT COUNT(*) FROM entity_tags").fetchone()[0],
            "tag_fts_rows": connection.execute("SELECT COUNT(*) FROM tags_fts").fetchone()[0],
            "tagged_entities": connection.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT entity_type, entity_id FROM entity_tags)"
            ).fetchone()[0],
            "species_dex_memberships": connection.execute(
                "SELECT COUNT(*) FROM species_dex_numbers"
            ).fetchone()[0],
            "moves_with_any_zh_description": connection.execute(
                """SELECT COUNT(*) FROM moves m WHERE
                   EXISTS (SELECT 1 FROM move_flavor_text f WHERE f.move_id = m.id AND f.language_id = 12)
                   OR EXISTS (SELECT 1 FROM entity_descriptions d WHERE d.entity_type = 'move'
                              AND d.entity_id = m.id AND d.language_id = 12)"""
            ).fetchone()[0],
            "abilities_with_any_zh_description": connection.execute(
                """SELECT COUNT(*) FROM abilities a WHERE
                   EXISTS (SELECT 1 FROM ability_flavor_text f WHERE f.ability_id = a.id AND f.language_id = 12)
                   OR EXISTS (SELECT 1 FROM entity_descriptions d WHERE d.entity_type = 'ability'
                              AND d.entity_id = a.id AND d.language_id = 12)"""
            ).fetchone()[0],
            "items_with_any_zh_description": connection.execute(
                """SELECT COUNT(*) FROM items i WHERE
                   EXISTS (SELECT 1 FROM item_flavor_text f WHERE f.item_id = i.id AND f.language_id = 12)
                   OR EXISTS (SELECT 1 FROM entity_descriptions d WHERE d.entity_type = 'item'
                              AND d.entity_id = i.id AND d.language_id = 12)"""
            ).fetchone()[0],
            "pokemon_item_relations": connection.execute("SELECT COUNT(*) FROM pokemon_items").fetchone()[0],
            "pokemon_type_relations": connection.execute("SELECT COUNT(*) FROM pokemon_types").fetchone()[0],
            "pokemon_stat_relations": connection.execute("SELECT COUNT(*) FROM pokemon_stats").fetchone()[0],
            "pokemon_ability_relations": connection.execute("SELECT COUNT(*) FROM pokemon_abilities").fetchone()[0],
            "learnset_rows": connection.execute("SELECT COUNT(*) FROM learnsets").fetchone()[0],
            "evolution_rows": connection.execute("SELECT COUNT(*) FROM evolutions").fetchone()[0],
            "foreign_key_violations": len(list(connection.execute("PRAGMA foreign_key_check"))),
        }
        source = dict(connection.execute("SELECT * FROM source_snapshots").fetchone())

    if counts["pilot_species"] != 20:
        issues.append({"check": "pilot_species_count", "actual": counts["pilot_species"], "expected": 20})
    if counts["species_catalog"] < 1000:
        issues.append({"check": "species_catalog_minimum", "actual": counts["species_catalog"], "expected_minimum": 1000})
    if counts["detailed_species"] != counts["species_catalog"]:
        issues.append(
            {
                "check": "detailed_species_coverage",
                "actual": counts["detailed_species"],
                "expected": counts["species_catalog"],
            }
        )
    if counts["species_with_zh_name"] != counts["species_catalog"]:
        issues.append(
            {
                "check": "species_zh_name_coverage",
                "actual": counts["species_with_zh_name"],
                "expected": counts["species_catalog"],
            }
        )
    for coverage_name in (
        "species_with_any_flavor_text",
        "species_with_move_relations",
        "species_with_ability_relations",
    ):
        if counts[coverage_name] != counts["species_catalog"]:
            issues.append(
                {
                    "check": coverage_name,
                    "actual": counts[coverage_name],
                    "expected": counts["species_catalog"],
                }
            )
    if counts["foreign_key_violations"]:
        issues.append({"check": "foreign_key_violations", "actual": counts["foreign_key_violations"]})
    if counts["knowledge_passages"] != counts["knowledge_fts_rows"]:
        issues.append(
            {
                "check": "knowledge_fts_row_count",
                "actual": counts["knowledge_fts_rows"],
                "expected": counts["knowledge_passages"],
            }
        )
    if counts["tags"] != counts["tag_fts_rows"]:
        issues.append(
            {"check": "tag_fts_row_count", "actual": counts["tag_fts_rows"], "expected": counts["tags"]}
        )
    if counts["tagged_entities"] != counts["entities"]:
        issues.append(
            {"check": "tagged_entity_coverage", "actual": counts["tagged_entities"], "expected": counts["entities"]}
        )
    if counts["move_mechanic_categories"] != 37:
        issues.append(
            {
                "check": "move_mechanic_category_count",
                "actual": counts["move_mechanic_categories"],
                "expected": 37,
            }
        )
    if counts["moves_with_mechanic_coverage"] < 900:
        issues.append(
            {
                "check": "move_mechanic_coverage_minimum",
                "actual": counts["moves_with_mechanic_coverage"],
                "expected_minimum": 900,
            }
        )
    for category in ("contact", "slicing", "wind", "ballistics"):
        membership_count = counts[f"{category}_move_memberships"]
        if membership_count == 0:
            issues.append(
                {"check": "move_mechanic_category_membership", "category": category, "actual": 0}
            )
    report = {
        "schema_version": 10,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": not issues,
        "edition_id": "mainline",
        "source": source,
        "counts": counts,
        "issues": issues,
    }
    if write_report:
        settings.audit_path.parent.mkdir(parents=True, exist_ok=True)
        settings.audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
