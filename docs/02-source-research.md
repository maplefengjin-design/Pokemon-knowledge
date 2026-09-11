# 开源数据源调研

调研日期：2026-09-09。活跃度以调研当日能看到的上游提交为准；真正接入时必须保存固定 commit 和 SHA-256，不能依赖本文中的“最新”。

## 结论

可以避免从头解析网页。推荐组合是：

1. **主数据：PokéAPI/pokeapi 的 `data/v2/csv`。** 关系清晰，适合构建自己的规范化数据库。
2. **快速 JSON 方案：PokeAPI/api-data。** 它是 PokéAPI API 响应的静态 JSON 快照，并附带生成的 Schema，适合不想运行完整 PokéAPI 构建链的场景。
3. **历史版本参考：zhenga8533/pokedb。** 它将 PokéAPI 重组为按世代 JSON，并显式标记无法核实的历史字段；可以借鉴 Schema 和验证器，但不应代替对 PokéAPI 原始数据的审计。
4. **对战机制扩展：smogon/pokemon-showdown。** 适合规则引擎和世代机制，不应被称为官方资料。
5. **中文深度资料：pokemon-dataset-zh 仅作低优先级补充。** 它抓取自神奇宝贝百科，虽然仓库放置了 MIT License，但这不等于上游百科文字和官方图片已获得可再分发授权。

## 候选项目对比

| 项目 | 内容/格式 | 活跃度观察 | 许可证与权利风险 | 建议 |
|---|---|---|---|---|
| [PokeAPI/pokeapi](https://github.com/PokeAPI/pokeapi) | `data/v2/csv` 为关系型源数据；API/OpenAPI；多语言、物种、形态、招式、特性、道具、版本等 | 2026-09-09 仍有数据修复提交 | 代码 BSD-3-Clause；Pokémon 名称和内容仍涉及权利方 IP；社区项目、非官方 | **首选主数据**，固定 commit 后导入 |
| [PokeAPI/api-data](https://github.com/PokeAPI/api-data) | 完整静态 API JSON 与生成 Schema；路径形如 `data/api/v2/.../index.json` | 2026-09-09 由 updater bot 随主仓更新 | BSD-3-Clause；同样不代表 Pokémon 内容获得独立授权 | **首选 JSON 快照**，最能避免重复网页解析 |
| [zhenga8533/pokedb](https://github.com/zhenga8533/pokedb) | 按世代生成 ability/item/move/pokemon JSON，带索引、验证和 `unverified_historical_fields` | 最近可见提交为 2026-07-20 | MIT；运行时唯一来源仍是 PokéAPI | **推荐借鉴**历史 Schema、事务式发布和校验逻辑 |
| [smogon/pokemon-showdown](https://github.com/smogon/pokemon-showdown) | TypeScript 对战引擎，Gen 1–9 机制、招式、特性、学习面、规则集 | 2026-09-09 仍有提交 | 服务端代码 MIT；游戏数据/IP 与社区内容需另看权利 | 第二阶段用于确定性对战规则；不作为官方来源 |
| [veekun/pokedex](https://github.com/veekun/pokedex) | 大量游戏数据 CSV，可构建 SQLite；PokéAPI 的历史源头 | 最近可见提交停在 2021-06-22 | 软件 MIT；README 明示数据提取自游戏且使用风险由使用者承担 | 只做历史交叉验证，不作为当前主源 |
| [Luxcis/pokemon-dataset-zh](https://github.com/Luxcis/pokemon-dataset-zh) | 第 1–9 世代中文详情 JSON，包含宝可梦、招式、特性和大量图片 | 最近可见提交为 2025-01-05 | 仓库 MIT，但 README 明示数据抓取自神奇宝贝百科；图片和原文的授权链不清晰 | 仅内部补缺/比对；默认不向量化长文、不分发图片 |
| [sindresorhus/pokemon](https://github.com/sindresorhus/pokemon) | 各语言宝可梦名称 JSON，含 `zh-Hans`/`zh-Hant` | 2026-07-13 仍有名称修正 | MIT；内容范围很窄 | 可作名称/别名交叉检查；PokeAPI 已覆盖大部分需求 |
| [Pokedex100-News/pokedexEverything](https://github.com/Pokedex100-News/pokedexEverything) | 宣称单处完整 JSON、覆盖 1,025 只宝可梦 | 最近可见提交为 2025-08-03 | 仓库 MIT，但数据来源与版本语义不够透明 | 不作主源；“单 JSON”方便但不利于版本化和溯源 |

## PokéAPI 能否满足中文需求

能满足第一阶段的规范名称与大量结构化字段。其语言表明确包含：

- `zh-hant`（语言 ID 4）
- `zh-hans`（语言 ID 12）

例如其静态 `pokemon-species/25` JSON 同时包含皮卡丘的多语言名称、地区图鉴编号、蛋组、进化链和世代信息。需要注意：并非每种资源的每段说明文字都有完整中文翻译，因此必须生成“字段 × 语言 × 版本”的覆盖率报告，不能只看语言表就宣称中文完整。

当前实现已经导入 `move_flavor_text.csv`、`ability_flavor_text.csv` 和 `item_flavor_text.csv`。这些文本按版本组保存，问答默认选择最新可用的简体中文游戏内措辞，而不是优先使用仅有英文的机制 prose。当前官方游戏中文覆盖为：招式 826/937、特性 267/374、道具 1560/2223。

官方游戏文本缺失时，使用 [42arch/pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh) 中整理自神奇宝贝百科的中文说明补缺。合并后的覆盖为：招式 919/937、特性 307/374、道具 1845/2223。补缺文本会被标记为 `encyclopedia_summary`，绝不显示为官方文本。神奇宝贝百科原创文字采用 CC BY-NC-SA 3.0，因此该层必须署名、仅限非商业使用并相同方式共享；不能因为聚合仓库标注 MIT 就忽略原始文字许可。

当前已启用 Pokémon Showdown 的窄范围导入：固定提交 `d849b220082e113d8a17303509fb44d420c543d5`，只解析 `data/moves.ts` 的招式编号与 `flags`，并用 `sim/dex-moves.ts` 中的类型注释核验每个标记的语义。共核验 901 个本地招式，保留 37 个有机制意义的分类、3,171 条正向关系，其中 846 个招式至少有一个正向分类；`allyanim` 仅控制动画，未进入知识库。这一层用于判断接触、切割、风、球和弹等当前规则分类，并明确标记为社区规则实现，不替代官方规则文本。

参考：

- [PokéAPI V2 文档](https://pokeapi.github.io/pokeapi.co/v2/)
- [PokéAPI 数据来源与静态 JSON 架构说明](https://pokeapi.github.io/pokeapi.co/about/)
- [PokéAPI languages.csv](https://github.com/PokeAPI/pokeapi/blob/master/data/v2/csv/languages.csv)
- [api-data README](https://github.com/PokeAPI/api-data/blob/master/README.md)

## “官方信息”与“官方 API”必须区分

目前调研到的 PokéAPI、PokéDB、Showdown 和中文数据集都是社区项目。它们可能整理自官方游戏或官方文本，但不能标记为“由 Pokémon 官方提供”。

Pokemon.com 的官方图鉴确实包含属性、能力和图鉴描述，例如 [Pikachu 官方图鉴页](https://www.pokemon.com/us/pokedex/pikachu)，但 [Pokemon.com 使用条款](https://assets.pokemon.com/assets/cms2/pdf/trainer-club/pokemon_website_terms_of_use.pdf) 明确限制批量下载内容到数据库，[版权说明](https://www.pokemon.com/us/legal/copyright) 也要求默认认为站内内容受版权保护。因此建议：

- 不建立 Pokemon.com 自动爬虫，不保存整页和长篇原文。
- 官方公告、规则 PDF 和图鉴页仅登记 URL、发布日期、人工核验结论与必要的短摘要。
- 公开产品展示“来源：PokéAPI（社区整理）”或“经官方页面核验”，不要使用“官方 API”措辞。
- 若项目计划商业化、公开数据下载或再分发图片/文本，应在接入前取得许可或专业法律意见。

## 数据覆盖与冲突策略

### 来源等级

| 等级 | 含义 | 可否直接支持答案 |
|---|---|---|
| A | 获许可的官方数据/官方规则，或有记录的人工官方核验 | 可以 |
| B | PokéAPI 固定版本的结构化数据 | 可以，但标为社区整理 |
| C | PokéDB/Showdown 等可复现的社区派生数据 | 可以，需标来源与适用域 |
| D | 第三方百科抓取或来源不透明的聚合 JSON | 仅补缺/候选事实，最好二次核验 |
| E | 模型参数记忆或无来源文本 | 不可作为事实证据 |

### 冲突处理

- 不按来源数量投票；先比较作品域、形态、世代、游戏和发布日期。
- 同上下文冲突时保留两条记录并生成审计任务，不静默覆盖。
- 官方规则优先于社区实现；但官方描述未给精确数值时，不让模型自行补值。
- 历史字段被当前值回填时必须打 `unverified` 标记，并在回答中避免确定语气。

## 推荐的初始导入范围

从 PokéAPI 只导入这些资源即可完成有价值的 MVP：

- language、generation、version、version-group、pokedex
- pokemon-species、pokemon、pokemon-form
- type、stat、ability、move、move-learn-method
- evolution-chain、item（仅核心字段）

地点、遭遇表、机器编号、图像、叫声和长篇 flavor text 可以第二批导入。这样能先验证最难的“物种/形态/版本”模型，而不会被素材与版权问题拖住。

## PokeMMO 特别版补充调研

PokeMMO 没有与 PokéAPI 等价、由官方承诺稳定的公共知识 API。特别版应以官方论坛更新公告作为高优先级差异证据，再结合允许导出的客户端资料与社区结构化数据进行交叉验证。

| 来源 | 可借鉴内容 | 当前风险与处理 |
|---|---|---|
| [PokeMMO 官方论坛](https://forums.pokemmo.com/) | 官方更新、活动与机制改动的生效时间 | 首选差异证据；只采集明确公告和必要摘要，保存主题 URL、发布日期、作者/Staff 身份和内容哈希 |
| [PokeMMO-Tools/pokemmo-data](https://github.com/PokeMMO-Tools/pokemmo-data) | `monsters.json`、`moves.json`、`pokedex.json`、道具和本地化文件 | 社区项目且明确非官方；README 还说明部分内存/本地化导出不受客户端支持或可能违反 ToS。只考虑客户端明确支持导出的类别，其他数据默认禁用 |
| [PokeMMOZone/PokeMMO-Data](https://github.com/PokeMMOZone/PokeMMO-Data) | 单文件宝可梦、招式、特性、地点、可获得性与蛋招式 JSON | 2025-07-14 已归档，部分文件注明仍在开发；GPL-2.0。只作历史对照和解析器样例，不作当前主源 |
| [PokeMMO 工具站](https://tool.lzpoke.com/) | 中文图鉴、招式/特性、蛋组与遗传工具、遭遇和计算器 | 第三方且同时包含静态与实时数据；接入前需要许可/接口/条款审计，静态字段才能进入候选 overlay |
| Alphapedia | Alpha/明雷等社区数据 | 主要是实时数据，不进入静态知识库；未来只能通过带时间戳的 live adapter 查询 |

特别版的来源优先级建议为：PokeMMO 官方公告/规则 → 客户端明确允许导出的结构化数据 → 多个独立社区来源交叉验证 → 单一社区人工资料。任何来源都不能因为“JSON 可下载”就绕过其许可或 PokeMMO ToS。
