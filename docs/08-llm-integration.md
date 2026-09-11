# 大模型接口与测试

项目提供两个工具调用协议：

- `responses`：OpenAI Responses API，默认选择。
- `chat-completions`：兼容提供 `/v1/chat/completions` 和 function tools 的第三方或本地服务。

## 配置

双击项目根目录的 `配置大模型.cmd`，在打开的 `config/llm.env` 中填写：

```text
POKEMON_LLM_ENABLED=true
POKEMON_LLM_PROTOCOL=responses
POKEMON_LLM_BASE_URL=https://api.openai.com/v1
POKEMON_LLM_MODEL=你的模型标识
POKEMON_LLM_API_KEY=你的密钥
POKEMON_LLM_TRACE=true
```

保存后双击原有的 `启动项目.cmd`。启动标题应显示“大模型增强模式”。输入 `/mode` 可随时确认模式。

本地或第三方兼容服务通常需要改成：

```text
POKEMON_LLM_PROTOCOL=chat-completions
POKEMON_LLM_BASE_URL=http://127.0.0.1:端口/v1
POKEMON_LLM_MODEL=服务中加载的模型标识
POKEMON_LLM_API_KEY=
```

只有目标服务确实实现 function/tool calling 时，模型才能调用本项目的知识工具。

## 工作流程

1. 用户自然语言问题发送给大模型。
2. 大模型选择一个或多个只读知识工具。
3. 程序在本地 SQLite 中执行工具并返回 JSON 结果。
4. 大模型根据结果组织中文回答；整个过程最多进行六轮工具调用。
5. 对话保留最近六轮问答，用于处理“它呢”“和刚才那个相比”等上下文指代。

学习面问题未指定版本时，模型会省略 `version_group`，由本地工具分别选择每只宝可梦最新有学习面记录的版本。只有用户明确指定《朱／紫》等版本时才固定版本查询。

API 密钥只进入 HTTP `Authorization` 请求头，不写入提示词、工具参数或日志。`config/llm.env` 已加入 `.gitignore`。

测试阶段建议保留 `POKEMON_LLM_TRACE=true`。工具选择及参数不会显示在聊天窗口，而是追加写入 `logs/llm-tool-trace.jsonl`；改成 `false` 可以完全停止记录。

模型正文允许使用 Markdown。控制台当前会原样展示，后续接入 HTML 窗口时可由前端渲染；工具调用与日志格式不依赖具体展示层。

## 建议理解能力测试

- “不算传说和幻之宝可梦，速度比超梦快的有哪些？”
- “刚才这些里面，哪些拥有虫属性？”
- “土居忍士为什么能进化出两只宝可梦？分别需要什么条件？”
- “找出高威力电属性物理招式，再告诉我皮卡丘在朱紫能否学会。”
- “比较喷火龙、火焰鸡和烈焰猴，更适合物攻的是谁？只按种族值判断。”

测试时重点记录：是否选对工具、参数是否正确、是否正确追问版本、是否引用了工具结果、是否出现知识库外的编造。
