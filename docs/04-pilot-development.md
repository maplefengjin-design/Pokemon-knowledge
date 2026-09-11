# 官方版全图鉴构建说明

## 当前范围

数据库已从最初的 20 只回归试点扩展到固定 PokéAPI 快照中的全部 1,025 个全国图鉴物种。`config/pilot_species.json` 仍用于50条黄金断言和重点完整性回归，但不再限制详情导入范围。

当前导入全部物种及其形态、属性、当前与历史种族值、当前与历史特性、蛋组、进化条件、野生携带物和按版本学习面。物种图鉴还包含分类、颜色、外形、栖息地、成长速率和按游戏版本保存的多语言图鉴描述。

固定快照中的简体中文物种名称覆盖 1,025/1,025；游戏内简体中文图鉴描述覆盖 722/1,025。没有简体中文描述的物种不会显示英文或未经核验的机器翻译。

## 可复现构建

日常查询无需手动打开 PowerShell：双击项目根目录的 `启动项目.cmd` 即可。启动器会自动切换工作目录、检查 Python 和数据库，并打开连续中文问答界面；数据库缺失时会尝试从已经同步的 PokéAPI CSV 自动重建。

首次同步或开发时可使用 PowerShell：

```powershell
.\tools\sync_pokeapi.ps1
python .\tools\build_pilot.py
python .\tools\run_pilot_evaluation.py
python -m unittest discover -s tests -v
```

同步脚本固定到：

```text
PokeAPI/pokeapi
8dfd1e309d4a1ca11f10b185412ed7dc8dd2b310
```

构建采用临时数据库并在全部导入和外键检查通过后原子替换正式数据库。脚本名暂时保留 `build_pilot.py` 以兼容已有启动流程；CLI 同时提供 `pokemon-kb build-mainline`。原始 CSV、构建产物和索引默认不提交 Git；来源 manifest 与审计报告应提交。

## CLI 示例

安装为可编辑包后：

```powershell
python -m pip install -e .
pokemon-kb build-mainline
pokemon-kb audit
pokemon-kb evaluate
pokemon-kb search "皮卡丘"
pokemon-kb species "#25"
pokemon-kb learnset "皮卡丘" --version-group scarlet-violet
```

不安装时可以直接运行工具脚本完成构建和评测。当前 CLI 返回结构化 JSON，自由问答启动器已接入本地确定性回答层，但尚未调用通用大模型。

## 当前数据规模

- 全国图鉴物种：1,025，结构化详情覆盖率 100%；
- 宝可梦形态记录：1,351；形态展示记录：1,579；
- 宝可梦—属性关系：2,116；
- 宝可梦—种族值关系：8,106；
- 宝可梦—特性关系：2,941；
- 按版本学习面：638,321；
- 进化记录：553；
- 外键完整性问题：0。

## 数据产物

- `data/curated/pokemon_pilot.db`：完整主系列 SQLite 数据库，文件名为兼容旧启动器暂时保留
- `data/manifests/pokeapi-pilot.json`：commit、文件哈希、获取时间与解析器版本
- `data/manifests/pilot-audit.json`：覆盖率和完整性报告
- `evals/pilot_queries.json`：50 条确定性黄金断言

## 已知边界

- `_past` 表已原样保存，但部分摘要接口仍默认返回 PokéAPI 当前值；历史有效区间解析仍属于下一任务。
- 进化条件先按上游字段原样保存为 JSON，中文条件渲染尚未实现。
- 简体中文游戏内图鉴描述受上游覆盖限制，目前为722个物种。
- 尚未导入地点遭遇和图片；章节 FTS 已实现，向量检索、HTTP API 和大模型调用尚未实现。
- `mainline` 名称表示原作域，不代表数据是 Pokémon 官方 API。
