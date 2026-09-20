from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .query import KnowledgeService


# Provider-neutral JSON Schemas.  An LLM adapter can translate these to its
# function/tool-calling envelope without changing the knowledge service.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "lookup_entity",
        "description": "查询一个宝可梦、招式、特性或道具的结构化详情。招式详情通过 mechanic_categories 返回当前主系列机制分类；特性详情通过 mechanic_properties 返回交换、复制、压制、变身、破格、入场触发等显式 yes/no 状态，pilot_species 会列出包括 Mega 在内的持有形态并提供 display_name。只有相应 complete 字段为 true 时才可据此下结论。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "entity_type": {"enum": ["species", "move", "ability", "item"]},
            },
            "required": ["query", "entity_type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "filter_species",
        "description": "按形态的种族值、世代、属性、特性、标签或传说分类筛选宝可梦。form_scope 未传时为 all，包含 Mega 等非默认形态；只有用户明确要求基础/默认形态时才传 default，只要求 Mega 时传 mega。每个结果用 display_name 展示中文形态名。使用 ability_identifiers 时，matched_abilities 会明确返回命中特性的 slot 与 is_hidden；普通/隐藏特性判断必须以这些字段为准。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stat_filters": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "gt": {"type": "integer"}, "gte": {"type": "integer"},
                            "lt": {"type": "integer"}, "lte": {"type": "integer"},
                            "eq": {"type": "integer"},
                        },
                        "additionalProperties": False,
                    },
                },
                "generation_ids": {"type": "array", "items": {"type": "integer"}},
                "type_identifiers": {"type": "array", "items": {"type": "string"}},
                "ability_identifiers": {"type": "array", "items": {"type": "string"}},
                "tag_keys": {"type": "array", "items": {"type": "string"}},
                "form_scope": {"enum": ["all", "default", "mega"]},
                "ordinary_only": {"type": "boolean"},
                "sort_by": {"type": "string"},
                "descending": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_species",
        "description": "比较两种或更多宝可梦基础形态的种族值、身高、体重或捕获率。",
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                "fields": {
                    "type": "array",
                    "items": {"enum": ["hp", "attack", "defense", "special-attack", "special-defense", "speed", "base-stat-total", "height", "weight", "capture-rate", "base-happiness"]},
                    "minItems": 1,
                },
            },
            "required": ["queries", "fields"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_evolution_chain",
        "description": "取得宝可梦的完整进化链、方向、触发方式和条件字段。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_species_relations",
        "description": "一次取得宝可梦的形态、属性、特性、蛋组、进化、标签及学习面；未指定版本时自动采用该宝可梦最新有学习面记录的版本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "version_group": {"type": ["string", "null"]},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_species_learnset",
        "description": "查询一个宝可梦的招式学习面。用户未明确指定游戏版本时不要自行填版本，工具会自动选择该宝可梦最新登场且有学习面数据的版本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "version_group": {"type": ["string", "null"]},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "can_species_learn_move",
        "description": "精确判断一个宝可梦能否学习某招式。用户未明确指定游戏版本时不要自行填版本，工具会采用该宝可梦最新有学习面数据的版本。",
        "input_schema": {
            "type": "object",
            "properties": {
                "species_query": {"type": "string"},
                "move_query": {"type": "string"},
                "version_group": {"type": ["string", "null"]},
            },
            "required": ["species_query", "move_query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_entity_tags",
        "description": "列出实体的实用分类标签及每个标签的结构化生成证据；招式包含接触、切割、风、球和弹等分类，特性包含交换、扮演、复制、继承、找伙伴、压制、变身、破格和入场触发的正反标签。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "entity_type": {"enum": ["species", "move", "ability", "item"]},
            },
            "required": ["query", "entity_type"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_by_tags",
        "description": "按一个或多个标签查找宝可梦、招式、特性或道具，可组合检索招式机制分类，以及特性机制的正向或反向状态。",
        "input_schema": {
            "type": "object",
            "properties": {
                "tag_queries": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "entity_type": {"type": ["string", "null"], "enum": ["species", "move", "ability", "item", None]},
                "match_all": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["tag_queries"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lookup_battle_state",
        "description": "查询一个具体战斗状态的分类、作用范围、当前规则、持续时间/倍率等结构化参数、应对方式，以及设置或清除它的招式/特性。注意场地状态是广义概念：光墙、白雾属于队伍侧状态 side_condition，不属于四种 terrain。",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_battle_states",
        "description": "按类别或作用范围列出战斗状态，也可查询与某个招式/特性关联的状态。类别包括宝可梦自身状态、队伍侧场地状态、天气、四种场地、全场规则状态和气场。用户泛指场地状态时不要只查 terrain。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": ["string", "null"],
                    "enum": ["pokemon_status", "side_condition", "weather", "terrain", "field_condition", "aura", None],
                },
                "scope": {
                    "type": ["string", "null"],
                    "enum": ["pokemon", "side", "field", None],
                },
                "current_only": {"type": "boolean"},
                "related_entity_query": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_knowledge",
        "description": "检索复杂机制、失败条件、例外和世代差异的章节证据。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "version_group": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


@dataclass(frozen=True)
class KnowledgeTools:
    service: KnowledgeService

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "lookup_entity": self._lookup_entity,
            "filter_species": lambda args: self.service.filter_species(**args),
            "compare_species": lambda args: self.service.compare_species(**args),
            "get_evolution_chain": lambda args: self.service.get_evolution_chain(**args),
            "get_species_relations": lambda args: self.service.get_species_relations(**args),
            "get_species_learnset": lambda args: self.service.learnset(**args),
            "can_species_learn_move": lambda args: self.service.can_learn(**args),
            "get_entity_tags": lambda args: self.service.get_entity_tags(**args),
            "search_by_tags": lambda args: self.service.search_by_tags(**args),
            "lookup_battle_state": lambda args: self.service.battle_state_summary(**args),
            "list_battle_states": lambda args: self.service.list_battle_states(**args),
            "search_knowledge": self._search_knowledge,
        }
        try:
            handler = handlers[name]
        except KeyError as error:
            raise ValueError(f"Unknown knowledge tool: {name}") from error
        return handler(dict(arguments))

    def _lookup_entity(self, arguments: dict[str, Any]) -> Any:
        query = arguments["query"]
        entity_type = arguments["entity_type"]
        handlers = {
            "species": self.service.species_summary,
            "move": self.service.move_summary,
            "ability": self.service.ability_summary,
            "item": self.service.item_summary,
        }
        return handlers[entity_type](query)

    def _search_knowledge(self, arguments: dict[str, Any]) -> Any:
        query = arguments["query"]
        entities = self.service.find_entities_in_text(
            query, ("species", "move", "ability", "item")
        )
        return self.service.search_passages(
            query,
            linked_entities=entities,
            version_group=arguments.get("version_group") or "scarlet-violet",
            limit=arguments.get("limit", 5),
        )
