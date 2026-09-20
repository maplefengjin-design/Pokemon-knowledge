from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Settings
from .tool_api import KnowledgeTools, TOOL_DEFINITIONS


SYSTEM_PROMPT = """你是宝可梦主系列游戏知识助手。请始终使用简体中文回答。回答内容可以使用简洁的 Markdown 结构，以便终端、HTML 或其他前端按自身能力渲染。
涉及宝可梦、招式、特性、道具、种族值、学习面、进化或游戏机制的事实时，必须先调用提供的本地知识工具，不得依靠模型记忆猜测。
精确筛选使用 filter_species，多对象比较使用 compare_species，进化问题使用 get_evolution_chain，学习面列表使用 get_species_learnset，能否学会某招式使用 can_species_learn_move；具体异常状态、天气、场地、墙、撒钉、空间或气场先使用 lookup_battle_state，状态清单或“哪些状态与某招式关联”使用 list_battle_states；复杂机制、失败条件和历史差异使用 search_knowledge。
可按需要连续或并行调用多个工具。筛选和标签结果默认限制在 30 条以内；结果较多时说明只展示部分。
用户询问“哪些宝可梦”或按特性、属性、标签、种族值筛选且没有限定形态范围时，filter_species 必须省略 form_scope 或传 all，把 Mega 等非默认形态纳入结果；仅在用户明确说基础形态、普通形态或默认形态时传 default，仅查询 Mega 时传 mega。列举时优先使用 display_name，并明确区分默认形态和 Mega 等特殊形态。不能把 form_scope=default 的结果说成全部宝可梦。
普通特性、隐藏特性和特性槽位只能根据工具明确返回的 is_hidden、hidden 与 slot 字段判断；使用 filter_species 按特性筛选时，必须逐条读取 matched_abilities，不得根据模型记忆或同类宝可梦猜测。工具结果没有这些字段时，应追加调用 lookup_entity 或 get_species_relations，而不是补写结论。进化方式和进化条件只能根据 get_evolution_chain 返回的字段说明；用户没有询问时不要主动编造或扩展进化方法。
用户没有指定版本时，一般事实采用知识库当前主系列值；学习面问题绝对不要自行假定 scarlet-violet 或其他固定版本，也不必要求用户补充版本，应省略 version_group，让工具自动选择该宝可梦最新有学习面数据的版本。若用户明确指定版本，才传入该版本。`unavailable_in_version` 表示该版本没有此宝可梦的学习面，不能说成数据库资料不完整，也不能据此断言不能学习。
“场地状态”是广义概念，绝不能直接等同于四种场地 terrain。光墙、白雾、顺风和撒钉属于只影响一方的 side_condition；天气、四种 terrain、戏法空间等 field_condition、气场 aura 是彼此独立的分类。回答清单问题时先确认用户指的是哪一层；若用户泛指，则应列出这些分类并至少覆盖 side_condition 与 field_condition。
不要把英文数据库 identifier、内部版本键或工具调用过程写进回答，除非用户明确询问。区分可靠事实、资料缺失和推断，不要编造缺失信息。
回答应直接、清晰，只输出面向用户的结论和必要解释；最后单独一行注明“依据：本地宝可梦知识库”。"""


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def _http_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise LLMRequestError(f"大模型 API 返回 HTTP {error.code}：{detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise LLMRequestError(f"无法连接大模型 API：{error}") from error
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LLMRequestError("大模型 API 返回了无效 JSON。") from error
    if not isinstance(value, dict):
        raise LLMRequestError("大模型 API 返回格式不是 JSON 对象。")
    return value


@dataclass
class LLMClient:
    base_url: str
    api_key: str
    model: str
    protocol: str = "responses"
    timeout_seconds: float = 60.0
    transport: Transport = _http_transport
    request_deadline: float | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMClient":
        if not settings.llm_configured:
            raise LLMConfigurationError(
                "大模型尚未配置：请设置 POKEMON_LLM_ENABLED=true、"
                "POKEMON_LLM_MODEL 和 POKEMON_LLM_BASE_URL。"
            )
        if settings.llm_protocol not in {"responses", "chat-completions"}:
            raise LLMConfigurationError(
                "POKEMON_LLM_PROTOCOL 仅支持 responses 或 chat-completions。"
            )
        if "api.openai.com" in settings.llm_base_url and not settings.llm_api_key:
            raise LLMConfigurationError("连接 OpenAI API 时必须设置 POKEMON_LLM_API_KEY。")
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            protocol=settings.llm_protocol,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    @property
    def endpoint(self) -> str:
        suffix = "/responses" if self.protocol == "responses" else "/chat/completions"
        return self.base_url if self.base_url.endswith(suffix) else self.base_url + suffix

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = self.timeout_seconds
        if self.request_deadline is not None:
            remaining = self.request_deadline - time.monotonic()
            if remaining <= 0:
                raise LLMRequestError("大模型回答超过整题总时限，已终止模型调用。")
            timeout = min(timeout, max(0.1, remaining))
        return self.transport(self.endpoint, headers, payload, timeout)


def _responses_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }
        for tool in TOOL_DEFINITIONS
    ]


def _chat_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in TOOL_DEFINITIONS
    ]


def _tool_output(tools: KnowledgeTools, name: str, arguments_json: str) -> str:
    try:
        arguments = json.loads(arguments_json or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        if name in {"filter_species", "search_by_tags"}:
            arguments["limit"] = min(int(arguments.get("limit", 30)), 100)
        result = tools.call(name, arguments)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 60000:
            return json.dumps(
                {"truncated": True, "preview": encoded[:58000]},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return encoded
    except Exception as error:  # The model receives a recoverable tool error, never a crash.
        return json.dumps(
            {"error": error.__class__.__name__, "message": str(error)},
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass
class LLMAnswerEngine:
    client: LLMClient
    tools: KnowledgeTools
    max_tool_rounds: int = 6
    total_timeout_seconds: float | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    last_trace: list[dict[str, Any]] = field(default_factory=list)

    def answer(self, question: str) -> str:
        self.last_trace.clear()
        previous_deadline = self.client.request_deadline
        if self.total_timeout_seconds is not None:
            self.client.request_deadline = (
                time.monotonic() + self.total_timeout_seconds
            )
        try:
            if self.client.protocol == "responses":
                answer = self._answer_responses(question)
            else:
                answer = self._answer_chat_completions(question)
        finally:
            self.client.request_deadline = previous_deadline
        self.history.extend((
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ))
        self.history = self.history[-12:]
        return answer

    def _answer_responses(self, question: str) -> str:
        input_items: list[dict[str, Any]] = [*self.history, {"role": "user", "content": question}]
        for _ in range(self.max_tool_rounds + 1):
            response = self.client.post(
                {
                    "model": self.client.model,
                    "instructions": SYSTEM_PROMPT,
                    "input": input_items,
                    "tools": _responses_tools(),
                    "tool_choice": "auto",
                }
            )
            output = response.get("output", [])
            calls = [row for row in output if row.get("type") == "function_call"]
            if not calls:
                text = self._responses_text(response)
                if not text:
                    raise LLMRequestError("模型没有返回文本或工具调用。")
                return text
            input_items.extend(output)
            for call in calls:
                self._record_tool_call(call["name"], call.get("arguments", "{}"))
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": _tool_output(self.tools, call["name"], call.get("arguments", "{}")),
                    }
                )
        raise LLMRequestError("模型连续调用工具次数超过安全上限。")

    @staticmethod
    def _responses_text(response: dict[str, Any]) -> str:
        if response.get("output_text"):
            return str(response["output_text"]).strip()
        chunks: list[str] = []
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    chunks.append(str(content["text"]))
        return "\n".join(chunks).strip()

    def _answer_chat_completions(self, question: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self.history,
            {"role": "user", "content": question},
        ]
        for _ in range(self.max_tool_rounds + 1):
            response = self.client.post(
                {
                    "model": self.client.model,
                    "messages": messages,
                    "tools": _chat_tools(),
                    "tool_choice": "auto",
                }
            )
            try:
                message = response["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as error:
                raise LLMRequestError("Chat Completions 返回格式不完整。") from error
            calls = message.get("tool_calls") or []
            if not calls:
                text = message.get("content")
                if not text:
                    raise LLMRequestError("模型没有返回文本或工具调用。")
                return str(text).strip()
            messages.append(message)
            for call in calls:
                function = call.get("function", {})
                self._record_tool_call(function.get("name", ""), function.get("arguments", "{}"))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": _tool_output(
                            self.tools, function.get("name", ""), function.get("arguments", "{}")
                        ),
                    }
                )
        raise LLMRequestError("模型连续调用工具次数超过安全上限。")

    def _record_tool_call(self, name: str, arguments_json: str) -> None:
        try:
            arguments: Any = json.loads(arguments_json or "{}")
        except json.JSONDecodeError:
            arguments = {"raw": arguments_json}
        self.last_trace.append({"tool": name, "arguments": arguments})
