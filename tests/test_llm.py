from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pokemon_knowledge.llm import (  # noqa: E402
    LLMAnswerEngine,
    LLMClient,
    LLMRequestError,
    SYSTEM_PROMPT,
)


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return {"results": [{"name": "铁面忍者", "speed": 160}]}


class QueueTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        self.requests.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        return self.responses.pop(0)


class LLMAdapterTests(unittest.TestCase):
    def test_prompt_preserves_rich_text_and_requires_source(self) -> None:
        self.assertIn("Markdown", SYSTEM_PROMPT)
        self.assertIn("HTML", SYSTEM_PROMPT)
        self.assertIn("依据：本地宝可梦知识库", SYSTEM_PROMPT)

    def test_expired_total_deadline_stops_before_another_api_call(self) -> None:
        transport = QueueTransport([])
        client = LLMClient(
            "https://example.test/v1",
            "secret",
            "test-model",
            transport=transport,
        )
        engine = LLMAnswerEngine(
            client,
            FakeTools(),  # type: ignore[arg-type]
            total_timeout_seconds=-1,
        )
        with self.assertRaisesRegex(LLMRequestError, "总时限"):
            engine.answer("比较铁面忍者和龙头地鼠")
        self.assertEqual(transport.requests, [])

    def test_responses_tool_loop(self) -> None:
        transport = QueueTransport([
            {
                "output": [{
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "filter_species",
                    "arguments": json.dumps({
                        "stat_filters": {"speed": {"gt": 150}},
                        "ordinary_only": True,
                    }),
                }]
            },
            {"output_text": "速度超过150的普通宝可梦包括铁面忍者。"},
        ])
        tools = FakeTools()
        client = LLMClient("https://example.test/v1", "secret", "test-model", transport=transport)
        engine = LLMAnswerEngine(client, tools)  # type: ignore[arg-type]

        answer = engine.answer("有哪些速度特别快但不是传说的宝可梦？")

        self.assertIn("铁面忍者", answer)
        self.assertEqual(tools.calls[0][0], "filter_species")
        self.assertEqual(transport.requests[0]["url"], "https://example.test/v1/responses")
        self.assertNotIn("secret", json.dumps(transport.requests[0]["payload"]))
        tool_outputs = [
            row for row in transport.requests[1]["payload"]["input"]
            if row.get("type") == "function_call_output"
        ]
        self.assertEqual(tool_outputs[0]["call_id"], "call_1")
        self.assertIn("铁面忍者", tool_outputs[0]["output"])

    def test_chat_completions_tool_loop(self) -> None:
        transport = QueueTransport([
            {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "compare_species",
                            "arguments": '{"queries":["超梦","铁面忍者"],"fields":["speed"]}',
                        },
                    }],
                }}]
            },
            {"choices": [{"message": {"role": "assistant", "content": "铁面忍者更快。"}}]},
        ])
        tools = FakeTools()
        client = LLMClient(
            "http://127.0.0.1:1234/v1", "", "local-model",
            protocol="chat-completions", transport=transport,
        )
        engine = LLMAnswerEngine(client, tools)  # type: ignore[arg-type]

        answer = engine.answer("超梦和铁面忍者谁更快？")

        self.assertEqual(answer, "铁面忍者更快。")
        self.assertEqual(tools.calls[0][0], "compare_species")
        self.assertTrue(transport.requests[0]["url"].endswith("/chat/completions"))
        tool_messages = [
            row for row in transport.requests[1]["payload"]["messages"]
            if row.get("role") == "tool"
        ]
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_2")


if __name__ == "__main__":
    unittest.main()
