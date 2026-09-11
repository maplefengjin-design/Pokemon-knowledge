from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .query import KnowledgeService


# Provider-neutral JSON Schemas.  An LLM adapter can translate these to its
# function/tool-calling envelope without changing the knowledge service.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "lookup_entity",
        "description": "查询一个宝可梦、招式、特性或道具的结构化详情。招式详情中 mechanic_categories_complete 为 true 时，mechanic_categories 是当前主系列规则下的完整正向分类集合，集合中不存在某分类即表示该招式不属于该分类；为 false 时不得从缺席推断否定。",
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
        "description": "按基础形态种族值、世代、属性、特性、标签或传说分类筛选宝可梦。",
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
        "description": "列出实体的实用分类标签及每个标签的结构化生成证据；招式包含接触、切割、风、球和弹等当前机制分类。",
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
        "description": "按一个或多个标签查找宝可梦、招式、特性或道具，可组合检索接触、切割、风、球和弹等招式机制分类。",
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
