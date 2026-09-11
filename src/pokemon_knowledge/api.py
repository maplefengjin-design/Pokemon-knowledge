from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .chat import AnswerEngine
from .config import Settings
from .llm import LLMAnswerEngine, LLMClient
from .query import KnowledgeService
from .tool_api import KnowledgeTools


DOMAIN_MARKERS = (
    "宝可梦", "精灵", "图鉴", "属性", "特性", "招式", "技能", "进化",
    "种族值", "努力值", "个体值", "蛋组", "孵蛋", "道具", "物品",
    "捕获率", "学习面", "传说", "幻之", "异色", "闪光", "pokemmo",
)


@dataclass
class KnowledgeAnswerApplication:
    settings: Settings

    def __post_init__(self) -> None:
        self.service = KnowledgeService.from_settings(self.settings)
        self.local_engine = AnswerEngine(self.service)

    def _in_scope(self, question: str) -> bool:
        lowered = question.casefold()
        if any(marker.casefold() in lowered for marker in DOMAIN_MARKERS):
            return True
        entities = self.service.find_entities_in_text(
            question, ("species", "move", "ability", "item")
        )
        compact = "".join(character for character in lowered if character.isalnum())
        return any(
            int(entity.get("matched_length", 0)) >= 3
            or compact in {
                str(entity.get("name", "")).casefold(),
                str(entity.get("identifier", "")).casefold(),
            }
            for entity in entities
        )

    def answer(self, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")
        if len(question) > 500:
            raise ValueError("问题不能超过500个字符。")
        if not self._in_scope(question):
            return {
                "answer": "本服务仅回答宝可梦及PokeMMO相关问题。",
                "mode": "refused",
                "edition": self.settings.edition_id,
                "fallback_reason": "",
            }
        if (
            "pokemmo" in question.casefold()
            and self.settings.edition_id.casefold() != "pokemmo"
        ):
            return {
                "answer": (
                    "当前知识库尚未导入PokeMMO差异覆盖层，暂时不能可靠回答"
                    "PokeMMO专属规则；你仍可询问原作主系列资料。"
                ),
                "mode": "scope_unavailable",
                "edition": self.settings.edition_id,
                "fallback_reason": "",
            }

        if self.settings.llm_enabled:
            try:
                engine = LLMAnswerEngine(
                    LLMClient.from_settings(self.settings),
                    KnowledgeTools(self.service),
                    max_tool_rounds=self.settings.llm_max_tool_rounds,
                    total_timeout_seconds=self.settings.llm_total_timeout_seconds,
                )
                return {
                    "answer": engine.answer(question),
                    "mode": "llm",
                    "edition": self.settings.edition_id,
                    "fallback_reason": "",
                }
            except Exception as error:
                logging.warning("大模型问答失败，回退本地规则：%s", error)
                return self._local_answer(
                    question,
                    fallback_reason="大模型超时或暂时不可用，已使用本地知识库",
                )

        return self._local_answer(question)

    def _local_answer(
        self, question: str, fallback_reason: str = ""
    ) -> dict[str, Any]:
        try:
            answer = self.local_engine.answer(question)
            mode = "local"
        except Exception as error:
            logging.warning("本地知识库也无法处理本次问题：%s", error)
            answer = (
                "这个问题需要的资料或推理超出了当前知识服务能力，"
                "本次查询已终止。请尝试拆成更具体的单项问题。"
            )
            mode = "terminated"
            fallback_reason = "模型调用未完成，且本地规则无法生成可靠答案"
        return {
            "answer": answer,
            "mode": mode,
            "edition": self.settings.edition_id,
            "fallback_reason": fallback_reason,
        }


def _handler(application: KnowledgeAnswerApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PokemonKnowledge/0.1"

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/health":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "edition": application.settings.edition_id,
                    "llm_enabled": application.settings.llm_enabled,
                    "database": str(application.settings.database_path),
                },
            )

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/v1/answer":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16_384:
                    raise ValueError("请求正文大小无效。")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("请求正文必须是JSON对象。")
                question = payload.get("question")
                if not isinstance(question, str):
                    raise ValueError("question必须是字符串。")
                self._json(HTTPStatus.OK, application.answer(question))
            except (ValueError, json.JSONDecodeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception:
                logging.exception("知识问答接口处理失败。")
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "知识服务内部错误。"},
                )

        def log_message(self, format: str, *args: Any) -> None:
            logging.info("knowledge-api %s - %s", self.client_address[0], format % args)

    return Handler


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8766) -> None:
    application = KnowledgeAnswerApplication(settings)
    server = ThreadingHTTPServer((host, port), _handler(application))
    logging.info(
        "Pokemon知识服务已启动：http://%s:%s（edition=%s, llm=%s）",
        host,
        port,
        settings.edition_id,
        settings.llm_enabled,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Pokemon knowledge HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    serve(Settings.from_env(), args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
