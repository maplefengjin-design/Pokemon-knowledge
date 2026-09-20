from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pokemon_knowledge.audit import audit_pilot  # noqa: E402
from pokemon_knowledge.chat import AnswerEngine  # noqa: E402
from pokemon_knowledge.config import Settings  # noqa: E402
from pokemon_knowledge.evaluation import run_pilot_evaluation  # noqa: E402
from pokemon_knowledge.normalize import normalize_alias  # noqa: E402
from pokemon_knowledge.query import (  # noqa: E402
    AmbiguousEntityError,
    KnowledgeService,
    PilotDataUnavailableError,
)
from pokemon_knowledge.tool_api import KnowledgeTools, TOOL_DEFINITIONS  # noqa: E402


class PilotKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = Settings.from_env()
        if not cls.settings.database_path.exists():
            raise unittest.SkipTest("Run `python tools/build_pilot.py` before integration tests")
        cls.service = KnowledgeService.from_settings(cls.settings)
        cls.engine = AnswerEngine(cls.service)

    def test_normalize_alias(self) -> None:
        self.assertEqual(normalize_alias(" #００２５ "), "0025")

    def test_exact_chinese_resolution(self) -> None:
        result = self.service.resolve_species("皮卡丘")
        self.assertEqual([row["identifier"] for row in result], ["pikachu"])

    def test_genderless_alias_is_ambiguous(self) -> None:
        candidates = self.service.resolve_species("Nidoran")
        self.assertEqual([row["identifier"] for row in candidates], ["nidoran-f", "nidoran-m"])

    def test_ambiguous_summary_raises(self) -> None:
        with self.assertRaises(AmbiguousEntityError):
            self.service.species_summary("Nidoran")

    def test_non_pilot_species_now_has_full_details(self) -> None:
        result = self.service.resolve_species("charizard")
        self.assertEqual(result[0]["identifier"], "charizard")
        self.assertTrue(result[0]["pilot_ready"])
        summary = self.service.species_summary("charizard")
        default = next(row for row in summary["variants"] if row["default"])
        self.assertEqual([row["identifier"] for row in default["types"]], ["fire", "flying"])

    def test_pikachu_current_summary(self) -> None:
        summary = self.service.species_summary("25")
        default = next(row for row in summary["variants"] if row["default"])
        self.assertEqual(default["stats"]["speed"], 90)
        self.assertEqual([row["identifier"] for row in default["types"]], ["electric"])
        self.assertEqual(summary["source"]["upstream_commit"], "8dfd1e309d4a1ca11f10b185412ed7dc8dd2b310")

    def test_vulpix_has_regional_variant(self) -> None:
        summary = self.service.species_summary("vulpix")
        identifiers = {row["identifier"] for row in summary["variants"]}
        self.assertIn("vulpix-alola", identifiers)

    def test_version_specific_learnset(self) -> None:
        result = self.service.learnset("皮卡丘", "scarlet-violet")
        self.assertEqual(result["version_group"], "scarlet-violet")
        self.assertEqual(result["version_selection"], "user_specified")
        self.assertEqual(result["status"], "available")
        self.assertGreater(len(result["moves"]), 0)
        self.assertTrue(all("method_identifier" in row for row in result["moves"]))

    def test_unspecified_learnset_uses_species_latest_available_version(self) -> None:
        result = self.service.learnset("滚滚蝙蝠")
        self.assertEqual(result["version_group"], "sword-shield")
        self.assertEqual(result["version_selection"], "auto_latest_available")
        self.assertEqual(result["status"], "available")
        self.assertGreater(len(result["moves"]), 0)

    def test_absent_version_is_not_reported_as_incomplete_or_cannot_learn(self) -> None:
        learnset = self.service.learnset("滚滚蝙蝠", "scarlet-violet")
        self.assertEqual(learnset["status"], "unavailable_in_version")
        self.assertEqual(learnset["latest_available_version_group"], "sword-shield")
        self.assertEqual(learnset["moves"], [])
        result = self.service.can_learn("滚滚蝙蝠", "特性互换", "scarlet-violet")
        self.assertEqual(result["status"], "unavailable_in_version")
        self.assertIsNone(result["can_learn"])

    def test_can_learn_defaults_to_species_latest_available_version(self) -> None:
        result = self.service.can_learn("滚滚蝙蝠", "特性互换")
        self.assertEqual(result["version_group"], "sword-shield")
        self.assertEqual(result["version_selection"], "auto_latest_available")
        self.assertTrue(result["can_learn"])

    def test_unknown_version_group_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.learnset("皮卡丘", "not-a-real-version")

    def test_audit_passes(self) -> None:
        report = audit_pilot(self.settings, write_report=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["counts"]["foreign_key_violations"], 0)

    def test_fifty_case_evaluation_passes(self) -> None:
        result = run_pilot_evaluation(self.settings)
        self.assertEqual(result["total"], 50)
        self.assertEqual(result["passed"], 50)

    def test_paid_llm_is_disabled_by_default(self) -> None:
        self.assertFalse(Settings().llm_enabled)

    def test_move_is_an_independent_entity(self) -> None:
        move = self.service.move_summary("十万伏特")
        self.assertEqual(move["identifier"], "thunderbolt")
        self.assertEqual(move["power"], 90)
        self.assertEqual(move["type"]["identifier"], "electric")
        self.assertEqual(move["effect_language"], "zh-hans")
        self.assertEqual(move["description_kind"], "official_game_text")

    def test_current_move_mechanic_categories_are_structured(self) -> None:
        psycho_cut = self.service.move_summary("精神利刃")
        self.assertTrue(psycho_cut["mechanic_categories_complete"])
        categories = {row["identifier"] for row in psycho_cut["mechanic_categories"]}
        self.assertIn("slicing", categories)
        self.assertNotIn("contact", categories)
        self.assertTrue(
            all(row["source_id"] == "pokemon-showdown" for row in psycho_cut["mechanic_categories"])
        )

        air_cutter = self.service.move_summary("空气利刃")
        air_categories = {row["identifier"] for row in air_cutter["mechanic_categories"]}
        self.assertIn("slicing", air_categories)
        self.assertIn("wind", air_categories)

    def test_move_mechanic_categories_are_searchable_tags(self) -> None:
        slicing = self.service.search_by_tags(["切割类招式"], "move", match_all=True)
        slicing_ids = {row["identifier"] for row in slicing["results"]}
        self.assertIn("psycho-cut", slicing_ids)
        wind = self.service.search_by_tags(["风类招式"], "move", match_all=True)
        wind_ids = {row["identifier"] for row in wind["results"]}
        self.assertIn("air-cutter", wind_ids)
        self.assertEqual(wind["matched_tags"][0]["category"], "move_mechanic")
        self.assertIn("Pokémon Showdown", self.engine.answer("哪些是风类招式？"))

    def test_move_answer_displays_mechanic_categories_and_source(self) -> None:
        answer = self.engine.answer("精神利刃是不是接触类招式？")
        self.assertIn("切割类招式", answer)
        self.assertIn("不属于接触类招式", answer)
        self.assertIn("Pokémon Showdown", answer)

    def test_ability_is_an_independent_entity(self) -> None:
        ability = self.service.ability_summary("静电")
        self.assertEqual(ability["identifier"], "static")
        self.assertEqual(ability["effect_language"], "zh-hans")
        self.assertTrue(any(row["identifier"] == "pikachu" for row in ability["pilot_species"]))

    def test_ability_infobox_properties_are_explicit_and_complete(self) -> None:
        drizzle = self.service.ability_summary("降雨")
        self.assertTrue(drizzle["mechanic_properties_complete"])
        self.assertEqual(len(drizzle["mechanic_properties"]), 11)
        states = {
            row["identifier"]: row["state"]
            for row in drizzle["mechanic_properties"]
        }
        self.assertEqual(
            {
                key: states[key]
                for key in (
                    "skill-swap", "ability-change", "copyable",
                    "suppression", "transform", "entry",
                )
            },
            {
                "skill-swap": "yes",
                "ability-change": "yes",
                "copyable": "yes",
                "suppression": "yes",
                "transform": "yes",
                "entry": "yes",
            },
        )
        self.assertEqual(
            set(drizzle["mechanic_property_source_ids"]),
            {"pokemon-showdown", "pokemon-encyclopedia-ability-infobox"},
        )

    def test_species_specific_transform_rule_uses_infobox_not_flag_absence(self) -> None:
        multitype = self.service.ability_summary("多属性")
        states = {
            row["identifier"]: row["state"]
            for row in multitype["mechanic_properties"]
        }
        self.assertEqual(states["skill-swap"], "no")
        self.assertEqual(states["ability-change"], "no")
        self.assertEqual(states["copyable"], "no")
        self.assertEqual(states["suppression"], "no")
        self.assertEqual(states["transform"], "no")
        self.assertEqual(states["entry"], "no")

    def test_negative_ability_mechanic_tags_are_searchable(self) -> None:
        result = self.service.search_by_tags(
            ["不能被交换的特性"], "ability", match_all=True
        )
        identifiers = {row["identifier"] for row in result["results"]}
        self.assertIn("multitype", identifiers)
        self.assertIn("wonder-guard", identifiers)
        self.assertEqual(result["matched_tags"][0]["category"], "ability_mechanic")
        answer = self.engine.answer("哪些特性不能被交换？")
        self.assertIn("多属性", answer)
        self.assertIn("神奇宝贝百科", answer)
        verbose_answer = self.engine.answer("哪些特性不能被特性互换交换？")
        self.assertIn("多属性", verbose_answer)
        typo_answer = self.engine.answer("哪些特性不受破坏影响？")
        self.assertIn("按组合标签", typo_answer)
        self.assertIn("不受破格影响", typo_answer)

    def test_direct_ability_property_question_returns_one_precise_state(self) -> None:
        answer = self.engine.answer("多属性特性在变身时有效吗？")
        self.assertIn("变身时无效的特性", answer)
        self.assertIn("神奇宝贝百科", answer)

    def test_item_is_an_independent_entity(self) -> None:
        item = self.service.item_summary("吃剩的东西")
        self.assertEqual(item["identifier"], "leftovers")
        self.assertEqual(item["fling_power"], 10)
        self.assertEqual(item["effect_language"], "zh-hans")

    def test_cross_entity_learnset_question(self) -> None:
        result = self.service.can_learn("皮卡丘", "冲浪", "scarlet-violet")
        self.assertTrue(result["can_learn"])

    def test_free_form_answer_does_not_require_menu(self) -> None:
        answer = self.engine.answer("十万伏特是什么招式？")
        self.assertIn("威力 90", answer)
        self.assertNotIn("游戏内简体中文文本", answer)
        self.assertNotIn("Has a chance", answer)
        self.assertIn("本地 PokéAPI CSV 快照", answer)

    def test_species_answer_hides_internal_version_and_localizes_forms(self) -> None:
        answer = self.engine.answer("喷火龙的种族值")
        self.assertNotIn("游戏内简体中文文本", answer)
        self.assertNotIn("shield", answer)
        self.assertNotIn("charizard-mega", answer)
        self.assertNotIn("charizard-gmax", answer)
        self.assertIn("超级喷火龙Ｘ", answer)
        self.assertIn("超级喷火龙Ｙ", answer)
        self.assertIn("喷火龙（超极巨化形态）", answer)

    def test_encyclopedia_chinese_fills_missing_game_text(self) -> None:
        move = self.service.move_summary("dire-claw")
        self.assertEqual(move["description_kind"], "encyclopedia_summary")
        self.assertEqual(move["effect_language"], "zh-hans")
        answer = self.engine.answer("克命爪是什么招式？")
        self.assertIn("百科中文补缺文本", answer)
        self.assertIn("非商业使用", answer)

    def test_latest_item_mechanics_are_shown_by_default(self) -> None:
        item = self.service.item_summary("巢穴球")
        self.assertEqual(item["mechanics"]["generation_id"], 9)
        self.assertEqual(item["mechanics"]["parameters"]["maximum_multiplier"], 4.0)
        answer = self.engine.answer("巢穴球有什么用？")
        self.assertIn("第9世代", answer)
        self.assertIn("1级最高 4×", answer)

    def test_latest_ability_multiplier_is_shown_by_default(self) -> None:
        ability = self.service.ability_summary("强行")
        self.assertEqual(ability["mechanics"]["parameters"]["damage_multiplier"], 1.3)
        answer = self.engine.answer("强行特性加强多少？")
        self.assertIn("招式威力为 1.3×", answer)

    def test_life_orb_includes_boost_and_recoil(self) -> None:
        answer = self.engine.answer("生命宝珠的威力加强多少？")
        self.assertIn("威力为 1.3×", answer)
        self.assertIn("最大HP的 10%", answer)

    def test_explicit_older_version_does_not_use_latest_multiplier(self) -> None:
        answer = self.engine.answer("生命宝珠在黑白的威力加强多少？")
        self.assertIn("black-white", answer)
        self.assertIn("不套用第九世代默认倍率", answer)

    def test_section_rag_is_present_and_indexed(self) -> None:
        report = audit_pilot(self.settings, write_report=False)
        self.assertEqual(report["schema_version"], 12)
        self.assertEqual(report["counts"]["knowledge_documents"], 2)
        self.assertEqual(report["counts"]["knowledge_passages"], 6)
        self.assertEqual(
            report["counts"]["knowledge_passages"],
            report["counts"]["knowledge_fts_rows"],
        )

    def test_battle_state_categories_are_structured_and_audited(self) -> None:
        report = audit_pilot(self.settings, write_report=False)
        self.assertEqual(report["counts"]["battle_state_categories"], 6)
        self.assertEqual(report["counts"]["battle_states"], 59)
        self.assertEqual(report["counts"]["battle_states_with_aliases"], 59)
        self.assertEqual(report["counts"]["battle_state_relations"], 100)

    def test_battle_state_classification_distinguishes_side_terrain_and_field(self) -> None:
        light_screen = self.service.battle_state_summary("光墙")
        mist = self.service.battle_state_summary("白雾")
        grassy = self.service.battle_state_summary("青草场地")
        trick_room = self.service.battle_state_summary("戏法空间")
        rain = self.service.battle_state_summary("雨天")
        dark_aura = self.service.battle_state_summary("暗黑气场")
        self.assertEqual(light_screen["category"]["identifier"], "side_condition")
        self.assertEqual(mist["category"]["identifier"], "side_condition")
        self.assertEqual(grassy["category"]["identifier"], "terrain")
        self.assertEqual(trick_room["category"]["identifier"], "field_condition")
        self.assertEqual(rain["category"]["identifier"], "weather")
        self.assertEqual(dark_aura["category"]["identifier"], "aura")
        self.assertEqual(light_screen["parameters"]["duration_turns"], 5)

    def test_battle_states_can_be_queried_by_related_move(self) -> None:
        result = self.service.list_battle_states(related_entity_query="换场")
        identifiers = {row["identifier"] for row in result["results"]}
        self.assertIn("light-screen", identifiers)
        self.assertIn("mist", identifiers)
        self.assertIn("stealth-rock", identifiers)

    def test_local_chat_understands_broad_field_state(self) -> None:
        answer = self.engine.answer("本地知识库中有哪些场地状态？")
        self.assertIn("不会把“场地状态”只理解成", answer)
        self.assertIn("光墙", answer)
        self.assertIn("白雾", answer)
        self.assertIn("青草场地", answer)
        self.assertIn("戏法空间", answer)

    def test_chinese_fts_retrieves_without_entity_name(self) -> None:
        passages = self.service.search_passages("循环超过500次会怎样？")
        self.assertEqual(passages[0]["passage_id"], "metronome-current-selection")
        self.assertTrue(passages[0]["retrieval"]["fts_match"])
        self.assertIn("拍击", passages[0]["content"])

    def test_entity_linked_rag_handles_paraphrased_exclusion_question(self) -> None:
        question = "哪些招式不会被挥指用出？"
        entities = self.service.find_entities_in_text(
            question, ("species", "move", "ability", "item")
        )
        passages = self.service.search_passages(question, linked_entities=entities)
        self.assertEqual(passages[0]["passage_id"], "metronome-excluded-moves")
        self.assertTrue(passages[0]["retrieval"]["entity_link_match"])
        self.assertIn("守住", passages[0]["content"])

    def test_chat_uses_rag_for_metronome_exclusions(self) -> None:
        answer = self.engine.answer("哪些招式不会被挥指用出？")
        self.assertIn("按章节知识库检索", answer)
        self.assertIn("不会使出的招式", answer)
        self.assertIn("守住", answer)
        self.assertNotIn("威力 —", answer)

    def test_chat_uses_rag_for_encore_failure_conditions(self) -> None:
        answer = self.engine.answer("再来一次使用失败的招式有哪些？")
        self.assertIn("使用失败条件", answer)
        self.assertIn("PP已经为0", answer)
        self.assertIn("挥指", answer)

    def test_passage_scope_uses_requested_historical_generation(self) -> None:
        passages = self.service.search_passages(
            "再来一次在黑白持续几回合？", version_group="black-white"
        )
        self.assertEqual(passages[0]["passage_id"], "encore-generation-history")
        answer = self.engine.answer("再来一次在黑白持续几回合？")
        self.assertIn("第五世代起固定为3回合", answer)
        self.assertNotIn("第九世代的持续时间", answer)

    def test_all_species_have_structured_details(self) -> None:
        report = audit_pilot(self.settings, write_report=False)
        self.assertEqual(report["counts"]["species_catalog"], 1025)
        self.assertEqual(report["counts"]["detailed_species"], 1025)
        self.assertEqual(report["counts"]["species_with_zh_name"], 1025)
        self.assertEqual(report["counts"]["species_with_any_flavor_text"], 1025)
        self.assertEqual(report["counts"]["species_with_move_relations"], 1025)
        self.assertEqual(report["counts"]["species_with_ability_relations"], 1025)
        self.assertEqual(report["counts"]["pokemon_variants"], 1351)

    def test_full_species_move_and_ability_relations_are_loaded(self) -> None:
        report = audit_pilot(self.settings, write_report=False)
        self.assertEqual(report["counts"]["learnset_rows"], 638321)
        self.assertEqual(report["counts"]["pokemon_ability_relations"], 2941)
        self.assertEqual(report["counts"]["pokemon_stat_relations"], 8106)

    def test_last_national_dex_species_is_queryable(self) -> None:
        summary = self.service.species_summary("1025")
        self.assertEqual(summary["species"]["identifier"], "pecharunt")
        default = next(row for row in summary["variants"] if row["default"])
        self.assertEqual(len(default["stats"]), 6)
        self.assertGreaterEqual(len(default["abilities"]), 1)
        learnset = self.service.learnset("1025", "scarlet-violet")
        self.assertGreater(len(learnset["moves"]), 0)

    def test_species_pokedex_metadata_and_chinese_flavor_text(self) -> None:
        summary = self.service.species_summary("妙蛙种子")
        species = summary["species"]
        self.assertEqual(species["color"]["identifier"], "green")
        self.assertEqual(species["growth_rate"]["identifier"], "medium-slow")
        self.assertIsNotNone(species["description"])
        self.assertIsNotNone(species["description_game_version"])

    def test_reverse_move_to_species_relationship(self) -> None:
        result = self.service.move_learners("十万伏特", "scarlet-violet")
        identifiers = {row["species_identifier"] for row in result["learners"]}
        self.assertIn("pikachu", identifiers)
        self.assertGreater(len(identifiers), 20)
        answer = self.engine.answer("朱紫中哪些宝可梦能学十万伏特？")
        self.assertIn("种宝可梦拥有十万伏特的学习记录", answer)
        self.assertIn("皮卡丘", answer)

    def test_every_entity_has_practical_tags(self) -> None:
        report = audit_pilot(self.settings, write_report=False)
        self.assertEqual(report["counts"]["tagged_entities"], report["counts"]["entities"])
        self.assertEqual(report["counts"]["tags"], report["counts"]["tag_fts_rows"])
        self.assertGreater(report["counts"]["tags"], 2000)
        self.assertGreater(report["counts"]["entity_tags"], 40000)

    def test_mewtwo_wiki_style_tags(self) -> None:
        result = self.service.get_entity_tags("超梦", "species")
        labels = {row["label"] for row in result["tags"]}
        self.assertIn("基因宝可梦", labels)
        self.assertIn("拥有紧张感特性的宝可梦", labels)
        self.assertIn("关都地区图鉴宝可梦", labels)
        self.assertIn("卡洛斯地区图鉴宝可梦", labels)
        self.assertIn("种族值总和680的宝可梦", labels)
        self.assertIn("可以超级进化的宝可梦", labels)

    def test_combined_tag_search(self) -> None:
        result = self.service.search_by_tags(
            ["紫色宝可梦", "超能力属性宝可梦"], "species", match_all=True
        )
        identifiers = {row["identifier"] for row in result["results"]}
        self.assertIn("mewtwo", identifiers)
        self.assertEqual(len(result["matched_tags"]), 2)

    def test_species_filter_tool_uses_exact_sql(self) -> None:
        result = self.service.filter_species(
            stat_filters={"speed": {"gt": 150}},
            form_scope="default",
            ordinary_only=True,
            sort_by="speed",
            descending=True,
        )
        self.assertEqual(
            [(row["identifier"], row["stats"]["speed"]) for row in result["results"]],
            [("ninjask", 160), ("pheromosa", 151)],
        )

    def test_ability_filter_returns_authoritative_slots(self) -> None:
        result = self.service.filter_species(ability_identifiers=["huge-power"])
        self.assertTrue(result["ability_match_details_included"])
        matches = {
            row["pokemon_identifier"]: (
                row["matched_abilities"][0]["slot"],
                row["matched_abilities"][0]["is_hidden"],
            )
            for row in result["results"]
        }
        self.assertEqual(
            matches,
            {
                "starmie-mega": (1, False),
                "marill": (2, False),
                "azumarill": (2, False),
                "azurill": (2, False),
                "mawile-mega": (1, False),
                "bunnelby": (3, True),
                "diggersby": (3, True),
            },
        )
        self.assertEqual(result["form_scope"], "all")
        self.assertEqual(result["count"], 7)
        display_names = {
            row["pokemon_identifier"]: row["display_name"]
            for row in result["results"]
        }
        self.assertEqual(display_names["mawile-mega"], "超级大嘴娃")
        self.assertEqual(display_names["starmie-mega"], "超级宝石海星")

        default_only = self.service.filter_species(
            ability_identifiers=["huge-power"], form_scope="default"
        )
        self.assertEqual(default_only["count"], 5)
        self.assertFalse(any(row["is_mega"] for row in default_only["results"]))

    def test_ability_summary_owner_rows_include_form_and_slot(self) -> None:
        result = self.service.ability_summary("大力士")
        azurill = next(
            row for row in result["pilot_species"]
            if row["pokemon_identifier"] == "azurill"
        )
        self.assertEqual(azurill["slot"], 2)
        self.assertFalse(azurill["is_hidden"])
        self.assertTrue(azurill["is_default"])
        mega_starmie = next(
            row for row in result["pilot_species"]
            if row["pokemon_identifier"] == "starmie-mega"
        )
        self.assertEqual(mega_starmie["display_name"], "超级宝石海星")
        self.assertTrue(mega_starmie["is_mega"])

        starmie = self.service.species_summary("宝石海星")
        starmie_mega = next(
            row for row in starmie["variants"]
            if row["identifier"] == "starmie-mega"
        )
        self.assertEqual(starmie_mega["name"], "超级宝石海星")
        self.assertEqual(starmie_mega["forms"][0]["name"], "超级宝石海星")

    def test_species_comparison_tool(self) -> None:
        result = self.service.compare_species(["超梦", "铁面忍者"], ["speed"])
        self.assertEqual(result["comparisons"]["speed"]["leaders"], ["铁面忍者"])
        self.assertEqual(result["comparisons"]["speed"]["spread"], 30)
        answer = self.engine.answer("超梦和铁面忍者的速度种族值谁更快？")
        self.assertIn("铁面忍者", answer)
        self.assertIn("最大差值 30", answer)

    def test_evolution_chain_tool_includes_branches(self) -> None:
        result = self.service.get_evolution_chain("土居忍士")
        targets = {edge["to_name"] for edge in result["evolutions"]}
        self.assertEqual(targets, {"铁面忍者", "脱壳忍者"})
        answer = self.engine.answer("土居忍士的进化型是什么？")
        self.assertIn("Lv.20", answer)
        self.assertIn("特殊脱壳条件", answer)

    def test_provider_neutral_tool_contract_and_dispatch(self) -> None:
        self.assertGreaterEqual(len(TOOL_DEFINITIONS), 8)
        tools = KnowledgeTools(self.service)
        result = tools.call(
            "compare_species", {"queries": ["超梦", "铁面忍者"], "fields": ["speed"]}
        )
        self.assertEqual(result["comparisons"]["speed"]["maximum"], 160)

    def test_natural_language_combined_tag_search(self) -> None:
        answer = self.engine.answer("紫色且超能力属性的宝可梦有哪些？")
        self.assertIn("组合标签", answer)
        self.assertIn("超梦", answer)
        move_answer = self.engine.answer("高威力电属性招式有哪些？")
        self.assertIn("电属性招式 + 高威力招式", move_answer)
        self.assertIn("打雷", move_answer)

    def test_species_relations_tool_collects_major_domains(self) -> None:
        result = self.service.get_species_relations("超梦")
        self.assertIn("variants", result)
        self.assertIn("egg_groups", result)
        self.assertIn("pokedex_memberships", result)
        self.assertIn("evolution_chain", result)
        self.assertIn("tags", result)
        self.assertIn("learnset", result)
        self.assertEqual(result["learnset"]["version_selection"], "auto_latest_available")

    def test_learning_tools_are_exposed_to_llm(self) -> None:
        names = {tool["name"] for tool in TOOL_DEFINITIONS}
        self.assertIn("get_species_learnset", names)
        self.assertIn("can_species_learn_move", names)
        result = KnowledgeTools(self.service).call(
            "can_species_learn_move",
            {"species_query": "滚滚蝙蝠", "move_query": "特性互换"},
        )
        self.assertTrue(result["can_learn"])

    def test_battle_state_tools_are_exposed_to_llm(self) -> None:
        names = {tool["name"] for tool in TOOL_DEFINITIONS}
        self.assertIn("lookup_battle_state", names)
        self.assertIn("list_battle_states", names)
        result = KnowledgeTools(self.service).call(
            "lookup_battle_state", {"query": "灼伤"}
        )
        self.assertEqual(result["category"]["identifier"], "pokemon_status")

    def test_local_chat_uses_latest_available_learnset_without_version(self) -> None:
        answer = self.engine.answer("滚滚蝙蝠能学特性互换吗？")
        self.assertIn("自动采用", answer)
        self.assertIn("sword-shield", answer)
        self.assertIn("可以", answer)


if __name__ == "__main__":
    unittest.main()
