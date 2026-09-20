from __future__ import annotations

import re
from dataclasses import dataclass

from .query import KnowledgeService


VERSION_ALIASES = {
    "朱紫": "scarlet-violet",
    "朱/紫": "scarlet-violet",
    "剑盾": "sword-shield",
    "剑/盾": "sword-shield",
    "究极日月": "ultra-sun-ultra-moon",
    "日月": "sun-moon",
    "欧米伽红宝石阿尔法蓝宝石": "omega-ruby-alpha-sapphire",
    "oras": "omega-ruby-alpha-sapphire",
    "xy": "x-y",
    "黑2白2": "black-2-white-2",
    "黑白": "black-white",
    "心金魂银": "heartgold-soulsilver",
    "白金": "platinum",
    "钻石珍珠": "diamond-pearl",
    "绿宝石": "emerald",
    "火红叶绿": "firered-leafgreen",
}

ITEM_CATEGORY_ZH = {
    "held-items": "携带道具",
    "choice": "讲究类道具",
    "type-enhancement": "属性增强道具",
    "species-specific": "宝可梦专用道具",
    "effort-training": "基础点数训练道具",
    "medicine": "回复道具",
    "healing": "HP回复道具",
    "status-cures": "状态回复道具",
    "revival": "复活道具",
    "pp-recovery": "PP回复道具",
    "vitamins": "营养饮料",
    "evolution": "进化道具",
    "standard-balls": "精灵球",
    "special-balls": "特殊精灵球",
    "all-machines": "招式学习器",
    "mega-stones": "超级石",
    "z-crystals": "Z纯晶",
    "memories": "存储碟",
    "nature-mints": "薄荷",
    "tera-shard": "太晶碎块",
    "sandwich-ingredients": "三明治食材",
    "tm-materials": "招式学习器材料",
    "picnic": "野餐用品",
    "other": "其他道具",
}


@dataclass(frozen=True)
class AnswerEngine:
    """Small deterministic dialogue layer over the structured knowledge service.

    It deliberately refuses unsupported questions instead of asking a language
    model to invent facts.  A general LLM can later call the same service methods.
    """

    service: KnowledgeService

    @staticmethod
    def _first(rows: list[dict], entity_type: str) -> dict | None:
        return next((row for row in rows if row["entity_type"] == entity_type), None)

    @staticmethod
    def _version(question: str) -> str | None:
        lowered = question.casefold()
        for alias, identifier in VERSION_ALIASES.items():
            if alias.casefold() in lowered or identifier in lowered:
                return identifier
        return None

    @staticmethod
    def _stat_fields(question: str) -> list[str]:
        aliases = (
            ("种族值总和", "base-stat-total"), ("总种族值", "base-stat-total"),
            ("特攻", "special-attack"), ("特防", "special-defense"),
            ("速度", "speed"), ("攻击", "attack"), ("防御", "defense"),
            ("HP", "hp"), ("hp", "hp"),
        )
        return list(dict.fromkeys(identifier for alias, identifier in aliases if alias in question))

    @staticmethod
    def _source_note(source_id: str = "pokeapi-csv") -> str:
        if "pokemon-encyclopedia-ability-infobox" in source_id:
            description_note = (
                "中文效果说明来自 pokemon-dataset-zh 收录的神奇宝贝百科补缺文本"
                "（CC BY-NC-SA 3.0，非商业使用）；"
                if "pokemon-dataset-zh" in source_id
                else "结构化数值与中文名称来自本地 PokéAPI CSV 快照；"
            )
            showdown_note = (
                "扮演、复制、接球手、找伙伴与破格等细分机制来自固定提交的 "
                "Pokémon Showdown 开源规则数据；"
                if "pokemon-showdown" in source_id
                else ""
            )
            return (
                f"\n\n依据：{description_note}"
                "交换、覆盖、复制、无特性、变身及入场六项基本信息来自"
                "神奇宝贝百科逐页固定修订的信息框（CC BY-NC-SA 3.0，非商业使用）；"
                f"{showdown_note}当前回答属于原作主系列数据域。"
            )
        if source_id == "pokemon-dataset-zh+pokemon-showdown":
            return (
                "\n\n依据：中文效果说明来自 pokemon-dataset-zh 收录的神奇宝贝百科补缺文本"
                "（CC BY-NC-SA 3.0，非商业使用）；结构化数值来自本地 PokéAPI CSV 快照；"
                "当前主系列招式机制分类来自固定提交的 Pokémon Showdown 开源规则数据。"
            )
        if source_id == "pokeapi-csv+pokemon-showdown":
            return (
                "\n\n依据：结构化数值与中文名称来自本地 PokéAPI CSV 快照；"
                "当前主系列招式机制分类来自固定提交的 Pokémon Showdown 开源规则数据。"
            )
        if source_id == "pokemon-showdown":
            return (
                "\n\n依据：本地整理的主系列战斗状态知识；规则依据为固定提交的 "
                "Pokémon Showdown 开源规则数据，顶层数值默认采用当前主系列规则。"
            )
        if source_id == "pokemon-dataset-zh":
            return (
                "\n\n依据：pokemon-dataset-zh 收录的神奇宝贝百科中文补缺文本"
                "（CC BY-NC-SA 3.0，非商业使用）；结构化数值来自本地 PokéAPI CSV 快照。"
            )
        return "\n\n依据：本地 PokéAPI CSV 快照；当前回答属于原作主系列官方版数据域。"

    @staticmethod
    def _should_use_rag(question: str) -> bool:
        return any(
            cue in question
            for cue in (
                "不会", "不能选", "选不到", "选到", "失败", "例外", "排除",
                "为什么", "具体规则", "详细机制", "细节", "互动", "持续几回合",
                "历史变化", "世代变化", "循环", "500次",
            )
        )

    @staticmethod
    def _render_passage(passage: dict, requested_version: str | None) -> str:
        scope = requested_version or passage.get("version_group") or "当前主系列规则"
        source = passage["source"]
        return (
            f"按章节知识库检索（适用范围：{scope}）：\n"
            f"【{passage['document_title']} > {passage['section_path']}】\n"
            f"{passage['content']}\n\n"
            f"依据：{source['name']}，{source['url']}（{source['license']}；"
            f"核验日期 {source['retrieved_at']}）。本段是项目重新整理的机制摘要。"
        )

    def _render_species(self, entity: dict) -> str:
        summary = self.service.species_summary(entity["identifier"])
        species = summary["species"]
        variant = next(row for row in summary["variants"] if row["default"])
        types = " / ".join(row["name"] for row in variant["types"])
        abilities = "、".join(
            f"{row['name']}{'（隐藏特性）' if row['hidden'] else ''}"
            for row in variant["abilities"]
        )
        stat_labels = {
            "hp": "HP",
            "attack": "攻击",
            "defense": "防御",
            "special-attack": "特攻",
            "special-defense": "特防",
            "speed": "速度",
        }
        stats = "、".join(
            f"{stat_labels.get(key, key)} {value}" for key, value in variant["stats"].items()
        )
        text = (
            f"{species['name']}（全国图鉴 #{species['species_id']:04d}）是第"
            f"{species['introduced_generation']}世代引入的"
            f"{species['genus'] or '宝可梦'}。当前默认形态为{types}属性，"
            f"特性为{abilities}。身高 {variant['height_m']} m，体重 {variant['weight_kg']} kg，"
            f"捕获率 {species['capture_rate']}。种族值：{stats}，总和 {variant['base_stat_total']}。"
        )
        metadata = []
        for label, value in (
            ("颜色", species.get("color")),
            ("外形", species.get("shape")),
            ("栖息地", species.get("habitat")),
            ("成长速率", species.get("growth_rate")),
        ):
            if value:
                metadata.append(f"{label}：{value['name']}")
        if metadata:
            text += " " + "；".join(metadata) + "。"
        if species.get("description"):
            text += f" 图鉴描述：{species['description']}"
        other_variants = [row["name"] for row in summary["variants"] if not row["default"]]
        if other_variants:
            text += f" 其他已收录形态：{'、'.join(other_variants)}。"
        return text + self._source_note()

    def _render_move(self, entity: dict) -> str:
        move = self.service.move_summary(entity["identifier"])
        labels = {"physical": "物理", "special": "特殊", "status": "变化", "unknown": "未知"}
        power = "—" if move["power"] is None else str(move["power"])
        accuracy = "—" if move["accuracy"] is None else f"{move['accuracy']}%"
        text = (
            f"{move['name']}（{move['identifier']}）是{move['type']['name']}属性的"
            f"{labels[move['damage_class']]}招式：威力 {power}，命中 {accuracy}，PP {move['pp']}，"
            f"优先度 {move['priority']}。"
        )
        if move["short_effect"]:
            if move["effect_language"] == "zh-hans":
                text += f" 效果：{move['short_effect']}"
                if move["description_kind"] == "encyclopedia_summary":
                    text += "（百科中文补缺文本）"
            else:
                text += " 当前数据源暂未收录可靠的简体中文效果说明；为避免误导，不显示英文回退或机器翻译。"
        if move["mechanic_categories"]:
            labels = "、".join(category["label"] for category in move["mechanic_categories"])
            text += f" 机制分类（当前主系列规则）：{labels}。"
        source_id = move["description_source_id"]
        if move["mechanic_category_source_id"]:
            source_id = f"{source_id}+pokemon-showdown"
        return text + self._source_note(source_id)

    def _render_ability(self, entity: dict, requested_version: str | None = None) -> str:
        ability = self.service.ability_summary(entity["identifier"])
        text = f"{ability['name']}（{ability['identifier']}）是第 {ability['generation_id']} 世代引入的特性。"
        if ability["short_effect"]:
            if ability["effect_language"] == "zh-hans":
                text += f" 效果：{ability['short_effect']}"
                if ability["description_kind"] == "encyclopedia_summary":
                    text += "（百科中文补缺文本）"
            else:
                text += " 当前数据源暂未收录可靠的简体中文效果说明；为避免误导，不显示英文回退或机器翻译。"
        if ability["mechanics"] and requested_version in (None, ability["mechanics"]["version_group"]):
            mechanics = ability["mechanics"]
            text += (
                f" 当前默认精确机制（第{mechanics['generation_id']}世代"
                f"，{mechanics['version_group']}规则）：{mechanics['summary_zh']}"
            )
        elif ability["mechanics"] and requested_version:
            text += (
                f" 你指定了 {requested_version}；当前尚未导入该版本的独立精确参数，"
                "因此不套用第九世代默认倍率。"
            )
        if ability["mechanic_properties"]:
            labels = "；".join(
                prop["label"] for prop in ability["mechanic_properties"]
            )
            text += f" 机制信息（当前主系列规则）：{labels}。"
        if ability["pilot_species"]:
            owners = "、".join(
                f"{row['display_name']}{'（隐藏）' if row['is_hidden'] else ''}"
                for row in ability["pilot_species"][:12]
            )
            suffix = "等" if len(ability["pilot_species"]) > 12 else ""
            text += f" 在完整图鉴中，具有该特性的宝可梦包括：{owners}{suffix}。"
        source_ids = {
            ability["description_source_id"],
            *ability["mechanic_property_source_ids"],
        }
        source_id = "+".join(sorted(source_ids))
        return text + self._source_note(source_id)

    def _render_item(self, entity: dict, requested_version: str | None = None) -> str:
        item = self.service.item_summary(entity["identifier"])
        category = ITEM_CATEGORY_ZH.get(item["category"]["identifier"], item["category"]["name"])
        text = (
            f"{item['name']}（{item['identifier']}）是“{category}”类别道具，"
            f"位于“{item['pocket']['name']}”口袋；基础价格字段为 {item['cost']}。"
        )
        if item["short_effect"]:
            if item["effect_language"] == "zh-hans":
                text += f" 效果：{item['short_effect']}"
                if item["description_kind"] == "encyclopedia_summary":
                    text += "（百科中文补缺文本）"
            else:
                text += " 当前数据源暂未收录可靠的简体中文效果说明；为避免误导，不显示英文回退或机器翻译。"
        if item["mechanics"] and requested_version in (None, item["mechanics"]["version_group"]):
            mechanics = item["mechanics"]
            text += (
                f" 当前默认精确机制（第{mechanics['generation_id']}世代"
                f"，{mechanics['version_group']}规则）：{mechanics['summary_zh']}"
            )
        elif item["mechanics"] and requested_version:
            text += (
                f" 你指定了 {requested_version}；当前尚未导入该版本的独立精确参数，"
                "因此不套用第九世代默认倍率。"
            )
        if item["fling_power"] is not None:
            text += f" 投掷威力为 {item['fling_power']}。"
        return text + self._source_note(item["description_source_id"])

    def _render_battle_state(self, identifier: str) -> str:
        state = self.service.battle_state_summary(identifier)
        generation = f"第{state['generation_from']}世代起" if state["generation_from"] else ""
        if state["generation_to"]:
            generation = f"第{state['generation_from']}至第{state['generation_to']}世代"
        history = "（历史状态，当前标准规则不使用）" if not state["current"] else ""
        related = "、".join(
            f"{row['name']}（{row['note']}）" for row in state["related_entities"]
        )
        text = (
            f"{state['name']}属于“{state['category']['label']}”，作用范围为"
            f"{ {'pokemon': '单只宝可梦', 'side': '一方场地', 'field': '全场'}.get(state['scope'], state['scope']) }；"
            f"{generation}{history}。\n"
            f"说明：{state['description']}\n"
            f"当前机制：{state['mechanics']}"
        )
        if state["counterplay"]:
            text += f"\n应对方式：{state['counterplay']}"
        if related:
            text += f"\n相关招式/特性：{related}"
        return text + self._source_note("pokemon-showdown")

    def _render_battle_state_categories(self, categories: list[str]) -> str:
        category_info = {
            row["identifier"]: row
            for row in self.service.list_battle_states()["categories"]
        }
        blocks = []
        total = 0
        for category in categories:
            result = self.service.list_battle_states(category=category)
            total += result["count"]
            names = "、".join(row["name"] for row in result["results"])
            info = category_info[category]
            blocks.append(
                f"【{info['label']}】{info['description']}\n{names}"
            )
        introduction = (
            "本项目不会把“场地状态”只理解成电气/青草/薄雾/精神场地。"
            if set(categories) == {"side_condition", "terrain", "field_condition"}
            else ""
        )
        return (
            f"{introduction}当前收录这 {len(categories)} 类共 {total} 个当前状态：\n"
            + "\n\n".join(blocks)
            + self._source_note("pokemon-showdown")
        )

    def answer(self, question: str) -> str:
        question = question.strip()
        if not question:
            return "请输入一个宝可梦相关问题。"

        battle_states = self.service.find_battle_states_in_text(question)
        if (
            len(battle_states) == 1
            and not self._should_use_rag(question)
            and self._version(question) is None
        ):
            return self._render_battle_state(battle_states[0]["identifier"])

        state_list_intent = any(
            cue in question for cue in ("有哪些", "清单", "分类", "列出", "包括什么", "有什么状态")
        )
        if state_list_intent:
            if "天气" in question:
                return self._render_battle_state_categories(["weather"])
            if "气场" in question:
                return self._render_battle_state_categories(["aura"])
            if any(cue in question for cue in ("精灵状态", "宝可梦状态", "异常状态")):
                return self._render_battle_state_categories(["pokemon_status"])
            if "场地状态" in question:
                return self._render_battle_state_categories(
                    ["side_condition", "terrain", "field_condition"]
                )
            if "场地" in question:
                return self._render_battle_state_categories(["terrain"])

        found = self.service.find_entities_in_text(
            question, ("species", "move", "ability", "item")
        )
        species = self._first(found, "species")
        move = self._first(found, "move")
        ability = self._first(found, "ability")
        item = self._first(found, "item")
        species_entities = [row for row in found if row["entity_type"] == "species"]

        requested_version = self._version(question)
        if self._should_use_rag(question):
            passages = self.service.search_passages(
                question,
                linked_entities=found,
                version_group=requested_version or "scarlet-violet",
                limit=3,
            )
            if passages:
                return self._render_passage(passages[0], requested_version)

        comparison_intent = len(species_entities) >= 2 and any(
            cue in question for cue in ("谁更", "哪个更", "哪只更", "比较", "对比", "最高", "最低")
        )
        if comparison_intent:
            fields = self._stat_fields(question) or ["base-stat-total"]
            comparison = self.service.compare_species(
                [row["identifier"] for row in species_entities], fields
            )
            field_labels = {
                "hp": "HP", "attack": "攻击", "defense": "防御", "special-attack": "特攻",
                "special-defense": "特防", "speed": "速度", "base-stat-total": "种族值总和",
                "height": "身高", "weight": "体重", "capture-rate": "捕获率",
                "base-happiness": "基础亲密度",
            }
            lines = []
            for field in fields:
                values = "、".join(
                    f"{row['name']} {row['values'][field]}" for row in comparison["species"]
                )
                result = comparison["comparisons"][field]
                leaders = "、".join(result["leaders"])
                lines.append(
                    f"{field_labels[field]}：{values}；较高者为{leaders}，最大差值 {result['spread']}。"
                )
            return "按基础形态比较：" + " ".join(lines) + self._source_note()

        stat_aliases = {
            "种族值总和": "base-stat-total", "总种族值": "base-stat-total",
            "特攻": "special-attack", "特防": "special-defense", "速度": "speed",
            "攻击": "attack", "防御": "defense", "HP": "hp", "hp": "hp",
        }
        filter_match = re.search(
            r"(种族值总和|总种族值|特攻|特防|速度|攻击|防御|HP|hp)\s*"
            r"(超过|高于|大于|不低于|至少|低于|小于|不超过|至多|等于|为)\s*(\d+)",
            question,
        )
        if filter_match and any(word in question for word in ("哪些", "筛选", "列出", "有谁")):
            stat_name, operator_text, value_text = filter_match.groups()
            operator = {
                "超过": "gt", "高于": "gt", "大于": "gt", "不低于": "gte", "至少": "gte",
                "低于": "lt", "小于": "lt", "不超过": "lte", "至多": "lte", "等于": "eq", "为": "eq",
            }[operator_text]
            stat = stat_aliases[stat_name]
            if any(word in question for word in ("基础形态", "普通形态", "默认形态")):
                form_scope = "default"
            elif any(word in question for word in ("Mega", "mega", "超级形态", "超级进化")):
                form_scope = "mega"
            else:
                form_scope = "all"
            result = self.service.filter_species(
                stat_filters={stat: {operator: int(value_text)}},
                form_scope=form_scope,
                ordinary_only="普通" in question,
                sort_by=stat,
                descending=operator in {"gt", "gte"},
                limit=200,
            )
            field_label = stat_name
            rendered = "、".join(
                f"{row['display_name']}（{row['base_stat_total'] if stat == 'base-stat-total' else row['stats'][stat]}）"
                for row in result["results"]
            )
            scope_note = (
                "这里将“普通宝可梦”解释为非传说且非幻之宝可梦。" if result["ordinary_definition"] else ""
            )
            scope_label = {
                "all": "全部",
                "default": "默认",
                "mega": "Mega",
            }[result["form_scope"]]
            return (
                f"按{scope_label}形态范围的{field_label}筛选，共找到 {result['count']} 个形态：{rendered}。"
                f"{scope_note}"
            ) + self._source_note()

        if species and any(word in question for word in ("进化型", "进化成", "进化链", "进化条件", "由什么进化")):
            chain = self.service.get_evolution_chain(species["identifier"])
            focus_id = chain["focus"]["species_id"]
            outgoing = [edge for edge in chain["evolutions"] if edge["from_species_id"] == focus_id]
            incoming = [edge for edge in chain["evolutions"] if edge["to_species_id"] == focus_id]
            parts = []
            if outgoing:
                rendered = []
                for edge in outgoing:
                    conditions = edge["conditions"]
                    if edge["trigger"] == "level-up" and conditions.get("minimum_level"):
                        detail = f"等级提升至 Lv.{conditions['minimum_level']}"
                    elif edge["trigger"] == "shed":
                        detail = "特殊脱壳条件"
                    elif edge["trigger"] == "trade":
                        detail = "交换"
                    elif edge["trigger"] == "use-item":
                        detail = "使用进化道具"
                    else:
                        detail = edge["trigger"]
                    rendered.append(f"{edge['to_name']}（{detail}）")
                parts.append("可以进化为：" + "、".join(rendered))
            if incoming:
                parts.append("进化前形态为：" + "、".join(edge["from_name"] for edge in incoming))
            if not parts:
                parts.append("当前结构化进化表中没有直接相连的进化记录")
            return f"{chain['focus']['name']}" + "；".join(parts) + "。" + self._source_note()

        if found and any(word in question for word in ("标签", "分类标签", "实用分类")):
            best = found[0]
            tagged = self.service.get_entity_tags(best["identifier"], best["entity_type"])
            labels = "、".join(row["label"] for row in tagged["tags"])
            return f"{tagged['name']}目前有 {len(tagged['tags'])} 个结构化标签：{labels}。"

        mechanic_check_intent = move and any(
            cue in question for cue in ("是不是", "是否", "属于", "算不算", "算是")
        )
        if mechanic_check_intent:
            asked_categories = [
                row
                for row in self.service.match_tags_in_text(question, "move")
                if row["category"] == "move_mechanic"
            ]
            if asked_categories:
                summary = self.service.move_summary(move["identifier"])
                if not summary["mechanic_categories_complete"]:
                    labels = "、".join(row["label"] for row in asked_categories)
                    return (
                        f"当前机制分类源尚未覆盖{summary['name']}，因此不能根据标签缺席判断它是否属于{labels}。"
                        + self._source_note()
                    )
                actual_keys = {
                    f"move:mechanic:{row['identifier']}"
                    for row in summary["mechanic_categories"]
                }
                judgements = "；".join(
                    f"{'属于' if row['key'] in actual_keys else '不属于'}{row['label']}"
                    for row in asked_categories
                )
                positives = "、".join(row["label"] for row in summary["mechanic_categories"])
                return (
                    f"按当前主系列规则，{summary['name']}{judgements}。"
                    f"其完整正向机制分类为：{positives or '无'}。"
                    + self._source_note("pokeapi-csv+pokemon-showdown")
                )

        ability_property_cues = {
            "skill-swap": ("特性互换", "交换"),
            "role-play": ("扮演",),
            "trace": ("复制特性",),
            "receiver": ("接球手", "化学之力"),
            "entrainment": ("找伙伴",),
            "suppression": ("无特性", "胃液", "化学变化气体", "压制"),
            "transform": ("变身",),
            "mold-breaker": ("破格", "涡轮火焰", "兆级电压"),
            "entry": ("入场", "登场"),
        }
        if ability:
            requested_property = next(
                (
                    identifier
                    for identifier, cues in ability_property_cues.items()
                    if any(cue in question for cue in cues)
                ),
                None,
            )
            if requested_property is not None:
                summary = self.service.ability_summary(ability["identifier"])
                if not summary["mechanic_properties_complete"]:
                    return (
                        f"当前规则源尚未覆盖{summary['name']}，不能把机制标签缺失当作否定结论。"
                        + self._source_note()
                    )
                prop = next(
                    row
                    for row in summary["mechanic_properties"]
                    if row["identifier"] == requested_property
                )
                return (
                    f"按当前主系列规则，{summary['name']}属于“{prop['label']}”。"
                    f"{prop['description']}"
                    + self._source_note(f"pokeapi-csv+{prop['source_id']}")
                )

        tag_search_intent = any(word in question for word in ("哪些", "有哪些", "查找", "筛选", "列出"))
        if tag_search_intent:
            if any(word in question for word in ("招式", "技能")):
                tag_entity_type = "move"
            elif "特性" in question:
                tag_entity_type = "ability"
            elif any(word in question for word in ("道具", "物品")):
                tag_entity_type = "item"
            else:
                tag_entity_type = "species"
            matched_tags = self.service.match_tags_in_text(question, tag_entity_type)
            if matched_tags:
                result = self.service.search_by_tags(
                    [row["key"] for row in matched_tags], tag_entity_type, match_all=True, limit=200
                )
                tag_labels = " + ".join(row["label"] for row in result["matched_tags"])
                rendered = "、".join(row["name"] for row in result["results"][:40])
                suffix = "……" if result["count"] > 40 else ""
                return (
                    f"按组合标签“{tag_labels}”找到 {result['count']} 个结果：{rendered}{suffix}。"
                ) + self._source_note(
                    (
                        "pokeapi-csv+pokemon-showdown+"
                        "pokemon-encyclopedia-ability-infobox"
                        if any(
                            row["category"] == "ability_mechanic"
                            for row in result["matched_tags"]
                        )
                        else "pokeapi-csv+pokemon-showdown"
                        if any(
                            row["category"] == "move_mechanic"
                            for row in result["matched_tags"]
                        )
                        else "pokeapi-csv"
                    )
                )

        learn_intent = any(word in question for word in ("能学", "能不能学", "会不会", "学会", "学习"))
        reverse_learn_intent = move and not species and any(
            phrase in question
            for phrase in ("哪些宝可梦", "哪些精灵", "谁能学", "谁会", "谁可以学")
        )
        if reverse_learn_intent:
            version = requested_version
            if version is None:
                return (
                    "招式学习者会随游戏版本变化。请补充版本，例如："
                    f"“朱紫中哪些宝可梦能学{move['name']}？”"
                )
            result = self.service.move_learners(move["identifier"], version)
            unique_species: list[str] = []
            for row in result["learners"]:
                if row["species_name"] not in unique_species:
                    unique_species.append(row["species_name"])
            preview = "、".join(unique_species[:30])
            suffix = "……" if len(unique_species) > 30 else ""
            return (
                f"在 {version} 版本组中，共有 {len(unique_species)} 种宝可梦拥有"
                f"{result['move_name']}的学习记录。前 {min(30, len(unique_species))} 种为："
                f"{preview}{suffix}"
            ) + self._source_note()
        if learn_intent and species and move:
            result = self.service.can_learn(
                species["identifier"], move["identifier"], requested_version
            )
            version = result["version_group"]
            if result["status"] == "unavailable_in_version":
                return (
                    f"{species['name']}在你指定的 {version} 版本中没有学习面记录，"
                    f"因此不能据此判断它能否学习{move['name']}。该宝可梦最新有学习面记录的版本是"
                    f" {result['latest_available_version_group']}。"
                ) + self._source_note()
            automatic = (
                f"未指定版本，自动采用该宝可梦最新有学习面记录的 {version} 版本组。"
                if result["version_selection"] == "auto_latest_available"
                else ""
            )
            if result["can_learn"]:
                methods = "、".join(
                    row["method"] + (f" Lv.{row['level']}" if row["method"] == "level-up" else "")
                    for row in result["methods"]
                )
                answer = (
                    f"{automatic}可以。在 {version} 版本组中，{result['species_name']}可以学习"
                    f"{result['move_name']}；记录方式为：{methods}。"
                )
            else:
                answer = (
                    f"{automatic}按当前本地学习面记录，在 {version} 版本组中没有找到"
                    f"{result['species_name']}学习{result['move_name']}的记录。"
                )
            return answer + self._source_note()

        if species and not move and any(word in question for word in ("招式", "技能", "学习面")):
            result = self.service.learnset(species["identifier"], requested_version)
            version = result["version_group"]
            if result["status"] == "unavailable_in_version":
                return (
                    f"{species['name']}在你指定的 {version} 版本中没有学习面记录。"
                    f"该宝可梦最新有学习面记录的版本是 {result['latest_available_version_group']}；"
                    "这不表示数据库全局缺少它的学习面。"
                ) + self._source_note()
            unique_moves: list[str] = []
            methods: set[str] = set()
            for row in result["moves"]:
                methods.add(row["method_identifier"])
                if row["move_name"] not in unique_moves:
                    unique_moves.append(row["move_name"])
            preview = "、".join(unique_moves[:20])
            suffix = "……" if len(unique_moves) > 20 else ""
            return (
                (f"未指定版本，自动采用该宝可梦最新有学习面记录的 {version} 版本组。"
                 if result["version_selection"] == "auto_latest_available" else "")
                + f"{species['name']}在 {version} 版本组中有 {len(unique_moves)} 个去重后的招式记录，"
                f"获得方式包括：{'、'.join(sorted(methods))}。前 {min(20, len(unique_moves))} 个为："
                f"{preview}{suffix}"
            ) + self._source_note()

        if species and not item and any(word in question for word in ("携带", "持有", "掉落道具")):
            version = requested_version
            if version is None:
                return (
                    "野生携带物会随游戏版本变化。请补充版本，例如："
                    f"“{species['name']}在朱紫会携带什么道具？”"
                )
            result = self.service.held_items(species["identifier"], version)
            if not result["items"]:
                return (
                    f"当前本地数据没有记录{species['name']}在 {version} 版本组中的野生携带物。"
                ) + self._source_note()
            rendered = "、".join(
                f"{row['item_name']}（{row['game_version']}，稀有度字段 {row['rarity']}）"
                for row in result["items"]
            )
            return f"{species['name']}在 {version} 版本组的野生携带物记录为：{rendered}。" + self._source_note()

        if "特性" in question and species and not ability:
            return self._render_species(species)
        if any(word in question for word in ("招式", "技能", "威力", "命中", "pp", "PP")) and move:
            return self._render_move(move)
        if "特性" in question and ability:
            return self._render_ability(ability, requested_version)
        if any(word in question for word in ("道具", "物品", "携带", "投掷")) and item:
            return self._render_item(item, requested_version)

        # A bare exact or embedded entity name is still a useful natural query.
        if found:
            best = found[0]
            if best["entity_type"] == "species":
                return self._render_species(best)
            if best["entity_type"] == "move":
                return self._render_move(best)
            if best["entity_type"] == "ability":
                return self._render_ability(best, requested_version)
            if best["entity_type"] == "item":
                return self._render_item(best, requested_version)

        return (
            "我暂时无法从本地结构化知识库中确定这个问题。当前可直接询问全部图鉴宝可梦详情，"
            "或独立查询招式、特性、道具；涉及学习面时请注明游戏版本。"
        )
