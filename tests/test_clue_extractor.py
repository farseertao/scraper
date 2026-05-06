import unittest

from zj_agent.ai.clue_extractor import ClueExtractor
from zj_agent.ai.llm_client import LlmClient


class _FakeLlmClient(LlmClient):
    def __init__(self) -> None:
        super().__init__(
            provider="openai",
            api_key="demo-key",
            base_url="https://example.com/v1",
            model="demo-model",
            timeout_seconds=30,
        )

    def extract_structured(self, prompt: str) -> dict:
        return {
            "keep": True,
            "clue_type": "transfer",
            "investment_relevance": "high",
            "relevance_score": 87,
            "core_technology": "高效催化转化关键技术",
            "application_scenarios": ["绿色化工", "新材料制造"],
            "transformation_signals": ["专利", "中试"],
            "summary": "项目围绕高效催化转化形成中试基础，具备产业化潜力。",
            "reason_tags": ["成果转化", "关键技术"],
            "decision_reason": "技术对象明确，存在转化信号。",
        }


class ClueExtractorTests(unittest.TestCase):
    def test_extract_parses_structured_output(self) -> None:
        extractor = ClueExtractor(_FakeLlmClient())
        result = extractor.extract(
            title="重大科技成果转化项目",
            content="介绍关键技术与产业化进展。",
            attachment_names=["说明书.pdf"],
            source_name="测试来源",
        )
        assert result is not None
        self.assertTrue(result.keep)
        self.assertEqual("transfer", result.clue_type)
        self.assertEqual(87, result.relevance_score)
        self.assertIn("绿色化工", result.application_scenarios)
        self.assertIn("专利", result.transformation_signals)


if __name__ == "__main__":
    unittest.main()
