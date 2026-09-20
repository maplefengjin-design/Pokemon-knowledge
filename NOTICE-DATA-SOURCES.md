# 数据来源与许可提示

本项目代码与宝可梦相关数据的权利不是同一概念。数据库中的每条描述均保存 `source_id`，界面会按实际来源显示提示。

## PokéAPI

- 仓库：https://github.com/PokeAPI/pokeapi
- 用途：结构化数据、多语言名称、按版本保存的游戏内描述文本。
- 仓库许可：BSD-3-Clause；Pokémon 名称、角色和游戏内容仍属于相关权利方。

## pokemon-dataset-zh / 神奇宝贝百科

- 数据集：https://github.com/42arch/pokemon-dataset-zh
- 原始文字来源：https://wiki.52poke.com/
- 用途：仅在 PokéAPI 缺少简体中文游戏内描述时补缺。
- 许可处理：数据集仓库代码标记为 MIT，但其 README 说明内容抓取自神奇宝贝百科；百科原创文字默认采用 CC BY-NC-SA 3.0。因此本项目按更严格的 CC BY-NC-SA 3.0 处理这些补缺文本：须署名、仅限非商业使用、相同方式共享。
- 版权声明：https://wiki.52poke.com/wiki/神奇宝贝百科:版权声明

如果项目未来商业化，应禁用 `pokemon-dataset-zh` 补缺层，或取得相应许可并进行独立法律审查。数据库中的 `source_licenses.commercial_use_allowed` 可用于自动阻止不兼容来源进入商业构建。

## Pokémon Showdown

- 仓库：https://github.com/smogon/pokemon-showdown
- 固定提交：`d849b220082e113d8a17303509fb44d420c543d5`
- 用途：补充当前主系列规则中的招式机制分类、特性细分交互，以及宝可梦状态、队伍侧状态、天气、场地、空间效果与气场等对战条件。
- 许可：MIT；Copyright © 2011–2026 Guangcong Luo and Pokémon Showdown contributors。
- 限定：这是社区维护的对战规则实现，不表述为官方 API。项目只导入机械可判定的分类，排除仅用于界面动画的 `allyanim`。

## 神奇宝贝百科特性信息框

- 站点：https://wiki.52poke.com
- 用途：通过公开 MediaWiki API 提取特性信息框中的 `Skillswap`、`Change`、`Trace`、`Noability`、`Transform` 和 `Entry` 字段，生成交换、覆盖、复制、无特性、变身和入场六项显式状态。
- 可追溯性：`config/ability_infobox_mainline.json` 为每个页面记录 `page_id`、`revision_id`、修订时间及字段是否显式填写；默认值严格复现信息框模板逻辑。
- 许可：CC BY-NC-SA 3.0，须署名、仅限非商业使用、相同方式共享。
- 限定：该层是百科社区整理，不表述为官方 API；商业构建应禁用此层或另行取得许可并进行法律审查。
