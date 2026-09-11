from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pokemon_knowledge.api import KnowledgeAnswerApplication  # noqa: E402
from pokemon_knowledge.config import Settings  # noqa: E402
from pokemon_knowledge.llm import LLMRequestError  # noqa: E402


class KnowledgeAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = Settings(llm_enabled=False)
        cls.application = KnowledgeAnswerApplication(cls.settings)

    def test_local_answer_and_edition_are_returned(self) -> None:
        result = self.application.answer("皮卡丘的种族值是多少？")
        self.assertEqual(result["mode"], "local")
        self.assertEqual(result["edition"], "mainline")
        self.assertIn("皮卡丘", result["answer"])

    def test_non_pokemon_question_is_rejected_before_llm(self) -> None:
        result = self.application.answer("请帮我写一段Python代码")
        self.assertEqual(result["mode"], "refused")
        self.assertIn("仅回答宝可梦", result["answer"])

    def test_pokemmo_specific_question_is_not_answered_by_mainline_data(self) -> None:
        result = self.application.answer("PokeMMO里的皮卡丘捕获率是多少？")
        self.assertEqual(result["mode"], "scope_unavailable")
        self.assertIn("尚未导入PokeMMO差异覆盖层", result["answer"])

    def test_llm_failure_falls_back_to_local_engine(self) -> None:
        settings = Settings(
            llm_enabled=True,
            llm_model="test-model",
            llm_base_url="https://example.test/v1",
        )
        application = KnowledgeAnswerApplication(settings)
        with patch(
            "pokemon_knowledge.api.LLMAnswerEngine.answer",
            side_effect=LLMRequestError("offline"),
        ):
            result = application.answer("皮卡丘的种族值是多少？")
        self.assertEqual(result["mode"], "local")
        self.assertIn("皮卡丘", result["answer"])
        self.assertIn("大模型超时或暂时不可用", result["fallback_reason"])

    def test_local_failure_returns_terminal_response_instead_of_http_error(self) -> None:
        with patch(
            "pokemon_knowledge.api.AnswerEngine.answer",
            side_effect=ValueError("unsupported"),
        ):
            result = self.application.answer("皮卡丘和雷丘有什么复杂区别？")
        self.assertEqual(result["mode"], "terminated")
        self.assertIn("本次查询已终止", result["answer"])


if __name__ == "__main__":
    unittest.main()
