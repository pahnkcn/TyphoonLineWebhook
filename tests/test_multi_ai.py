"""
Tests for the Multi-AI Consensus Engine.

Tests cover:
- Provider auto-discovery from env vars
- Parallel generation (mocked)
- Evaluation JSON parsing
- Score aggregation
- Graceful degradation (single provider, provider failures)
- Full consensus flow
"""
import os
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

# Ensure test env vars are set before importing app modules
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("XAI_API_KEY", "test_xai_key")
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DB", "test_chatbot")

from app.llm.providers import AIProvider, ProviderRegistry, reset_registry
from app.llm.multi_ai import (
    _cooldown_before_evaluation,
    _parse_evaluation_json,
    _build_evaluation_prompt,
    aggregate_scores,
    select_best,
    ProviderResponse,
    EvaluationResult,
    ConsensusResult,
    generate_all,
    cross_evaluate,
    multi_ai_chat,
)


# ============================================================
# Provider Discovery Tests
# ============================================================

class TestAIProvider:
    def test_provider_enabled_with_key(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-abc123"}):
            p = AIProvider(
                name="test",
                api_key_env="TEST_KEY",
                base_url="https://example.com/v1",
                default_model="test-model",
            )
            assert p.enabled is True
            assert p.api_key == "sk-abc123"
            assert p.model == "test-model"

    def test_provider_disabled_without_key(self):
        env = os.environ.copy()
        env.pop("MISSING_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            p = AIProvider(
                name="test",
                api_key_env="MISSING_KEY",
                base_url="https://example.com/v1",
                default_model="test-model",
            )
            assert p.enabled is False
            assert p.api_key == ""

    def test_provider_custom_model_from_env(self):
        with patch.dict(os.environ, {
            "TEST_KEY": "sk-abc",
            "TEST_MODEL": "custom-model-v2",
        }):
            p = AIProvider(
                name="test",
                api_key_env="TEST_KEY",
                base_url="https://example.com/v1",
                default_model="test-model",
                model_env="TEST_MODEL",
            )
            assert p.model == "custom-model-v2"

    def test_provider_strips_whitespace(self):
        with patch.dict(os.environ, {"TEST_KEY": "  sk-abc  "}):
            p = AIProvider(
                name="test",
                api_key_env="TEST_KEY",
                base_url="https://example.com/v1",
                default_model="test-model",
            )
            assert p.api_key == "sk-abc"
            assert p.enabled is True


class TestProviderRegistry:
    def setup_method(self):
        reset_registry()

    def test_discovers_providers_with_keys(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-anthropic",
            "GEMINI_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "MOONSHOT_API_KEY": "",
        }):
            reg = ProviderRegistry()
            active = reg.get_active_providers()
            names = [p.name for p in active]
            assert "chatgpt" in names
            assert "claude" in names
            # XAI_API_KEY is set in conftest
            assert "grok" in names
            assert reg.count >= 3

    def test_no_providers_without_keys(self):
        clean_env = {k: v for k, v in os.environ.items()}
        for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                     "XAI_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY"]:
            clean_env.pop(key, None)
        with patch.dict(os.environ, clean_env, clear=True):
            reg = ProviderRegistry()
            assert reg.count == 0

    def test_get_provider_by_name(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            reg = ProviderRegistry()
            p = reg.get_provider("chatgpt")
            if p:
                assert p.name == "chatgpt"


# ============================================================
# Evaluation JSON Parsing Tests
# ============================================================

class TestParseEvaluationJson:
    def test_clean_json(self):
        raw = '{"A": 85, "B": 72}'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert scores == {"A": 85.0, "B": 72.0}

    def test_json_in_code_block(self):
        raw = '```json\n{"A": 90, "B": 60}\n```'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert scores == {"A": 90.0, "B": 60.0}

    def test_json_with_surrounding_text(self):
        raw = 'Here are the scores:\n{"A": 78, "B": 65}\nThank you.'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert scores == {"A": 78.0, "B": 65.0}

    def test_fallback_regex_parsing(self):
        raw = 'A: 88\nB: 73\nC: 91'
        scores = _parse_evaluation_json(raw, ["A", "B", "C"])
        assert scores == {"A": 88.0, "B": 73.0, "C": 91.0}

    def test_score_clamping(self):
        raw = '{"A": 150, "B": -10}'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert scores["A"] == 100.0
        assert scores["B"] == 0.0

    def test_empty_response(self):
        scores = _parse_evaluation_json("", ["A", "B"])
        assert scores == {}

    def test_invalid_json(self):
        scores = _parse_evaluation_json("not json at all", ["A", "B"])
        assert scores == {}

    def test_partial_labels(self):
        raw = '{"A": 80}'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert "A" in scores
        assert "B" not in scores

    def test_partial_json_with_regex_fallback(self):
        """JSON has non-numeric value for A, regex recovers from surrounding text."""
        raw = 'คำตอบ A: 85 คะแนน\nคำตอบ B: 88 คะแนน\n{"A": "ดี", "B": 88}'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert scores["B"] == 88.0
        assert scores["A"] == 85.0

    def test_json_mixed_types_fallback(self):
        """JSON has one valid score, one invalid — regex recovers missing."""
        raw = 'Score: A: 75\n{"A": null, "B": 90}'
        scores = _parse_evaluation_json(raw, ["A", "B"])
        assert scores["B"] == 90.0
        assert scores["A"] == 75.0


# ============================================================
# Score Aggregation Tests
# ============================================================

class TestAggregateScores:
    def test_basic_aggregation(self):
        responses = {"chatgpt": "resp1", "claude": "resp2", "grok": "resp3"}
        evaluations = [
            EvaluationResult(
                evaluator_name="chatgpt",
                scores={"claude": 80, "grok": 70},
            ),
            EvaluationResult(
                evaluator_name="claude",
                scores={"chatgpt": 90, "grok": 60},
            ),
            EvaluationResult(
                evaluator_name="grok",
                scores={"chatgpt": 85, "claude": 75},
            ),
        ]
        avg = aggregate_scores(responses, evaluations)
        assert avg["chatgpt"] == pytest.approx(87.5)  # (90+85)/2
        assert avg["claude"] == pytest.approx(77.5)    # (80+75)/2
        assert avg["grok"] == pytest.approx(65.0)      # (70+60)/2

    def test_missing_evaluations_get_baseline(self):
        responses = {"chatgpt": "resp1", "claude": "resp2"}
        evaluations = [
            EvaluationResult(
                evaluator_name="chatgpt",
                scores={"claude": 80},
            ),
            # claude evaluation failed
            EvaluationResult(
                evaluator_name="claude",
                scores={},
                success=False,
                error="Timeout",
            ),
        ]
        avg = aggregate_scores(responses, evaluations)
        assert avg["claude"] == 80.0
        assert avg["chatgpt"] == 50.0  # baseline (no scores received)

    def test_empty_evaluations(self):
        responses = {"chatgpt": "resp1"}
        avg = aggregate_scores(responses, [])
        assert avg["chatgpt"] == 50.0


class TestSelectBest:
    def test_selects_highest_score(self):
        responses = {"chatgpt": "resp1", "claude": "resp2", "grok": "resp3"}
        scores = {"chatgpt": 85.0, "claude": 92.0, "grok": 78.0}
        name, content, score = select_best(responses, scores)
        assert name == "claude"
        assert content == "resp2"
        assert score == 92.0

    def test_empty_scores_returns_first(self):
        responses = {"chatgpt": "resp1", "claude": "resp2"}
        name, content, score = select_best(responses, {})
        assert name == "chatgpt"
        assert score == 0.0


# ============================================================
# Build Evaluation Prompt Tests
# ============================================================

class TestBuildEvaluationPrompt:
    def test_excludes_evaluator(self):
        responses = {"chatgpt": "resp1", "claude": "resp2", "grok": "resp3"}
        prompt, labels = _build_evaluation_prompt("hello", responses, "chatgpt")
        # chatgpt should not be in the labels (it's the evaluator)
        assert "chatgpt" not in labels.values()
        assert "claude" in labels.values()
        assert "grok" in labels.values()
        assert len(labels) == 2

    def test_labels_are_sequential(self):
        responses = {"chatgpt": "r1", "claude": "r2", "grok": "r3"}
        _, labels = _build_evaluation_prompt("hi", responses, "chatgpt")
        assert sorted(labels.keys()) == ["A", "B"]


# ============================================================
# Integration Tests (mocked API calls)
# ============================================================

def _make_mock_completion(content: str, prompt_tokens: int = 50, completion_tokens: int = 30):
    """Create a mock OpenAI chat completion response."""
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice.message = mock_message
    mock_resp.choices = [mock_choice]
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = prompt_tokens
    mock_usage.completion_tokens = completion_tokens
    mock_usage.total_tokens = prompt_tokens + completion_tokens
    mock_resp.usage = mock_usage
    return mock_resp


class TestGenerateAll:
    def test_parallel_generation_mocked(self):
        provider1 = AIProvider(
            name="mock1", api_key_env="X", base_url="http://x",
            default_model="m1",
        )
        provider1.api_key = "key1"
        provider1.enabled = True

        provider2 = AIProvider(
            name="mock2", api_key_env="Y", base_url="http://y",
            default_model="m2",
        )
        provider2.api_key = "key2"
        provider2.enabled = True

        registry = MagicMock(spec=ProviderRegistry)
        registry.get_active_providers.return_value = [provider1, provider2]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            "Test response"
        )
        registry.get_client.return_value = mock_client

        messages = [{"role": "user", "content": "hello"}]
        results = generate_all(messages, registry)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert all(r.content == "Test response" for r in results)


class TestMultiAiChat:
    def test_single_provider_skips_evaluation(self):
        provider1 = AIProvider(
            name="only_one", api_key_env="X", base_url="http://x",
            default_model="m1",
        )
        provider1.api_key = "key1"
        provider1.enabled = True

        registry = MagicMock(spec=ProviderRegistry)
        registry.get_active_providers.return_value = [provider1]
        registry.count = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(
            "Only response"
        )
        registry.get_client.return_value = mock_client

        messages = [{"role": "user", "content": "hello"}]
        result = multi_ai_chat(messages, registry)

        assert isinstance(result, ConsensusResult)
        assert result.best_response == "Only response"
        assert result.best_provider == "only_one"
        assert result.evaluation_rounds == 0

    def test_full_consensus_flow_mocked(self):
        provider1 = AIProvider(
            name="p1", api_key_env="X", base_url="http://x",
            default_model="m1",
        )
        provider1.api_key = "key1"
        provider1.enabled = True

        provider2 = AIProvider(
            name="p2", api_key_env="Y", base_url="http://y",
            default_model="m2",
        )
        provider2.api_key = "key2"
        provider2.enabled = True

        registry = MagicMock(spec=ProviderRegistry)
        registry.get_active_providers.return_value = [provider1, provider2]
        registry.count = 2

        # Different generation responses
        gen_responses = {
            "p1": "Response from P1",
            "p2": "Response from P2",
        }
        # Evaluation: p1 gives p2 score 70, p2 gives p1 score 90
        eval_responses = {
            "p1": '{"A": 70}',
            "p2": '{"A": 90}',
        }

        call_count = {"gen": 0, "eval": 0}

        def mock_create(**kwargs):
            messages = kwargs.get("messages", [])
            # Determine if this is a generation or evaluation call
            is_eval = any(
                "ประเมินคุณภาพ" in msg.get("content", "")
                for msg in messages
                if msg.get("role") == "system"
            )

            if is_eval:
                call_count["eval"] += 1
                # Return eval score based on which provider is evaluating
                # (we can't easily distinguish, return a fixed response)
                return _make_mock_completion('{"A": 85}')
            else:
                call_count["gen"] += 1
                return _make_mock_completion(f"Response #{call_count['gen']}")

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create
        registry.get_client.return_value = mock_client

        messages = [{"role": "user", "content": "test message"}]
        result = multi_ai_chat(messages, registry)

        assert isinstance(result, ConsensusResult)
        assert result.providers_used == 2
        assert result.best_response in ["Response #1", "Response #2"]
        assert result.total_time_ms > 0

    def test_all_providers_fail_raises(self):
        provider1 = AIProvider(
            name="fail1", api_key_env="X", base_url="http://x",
            default_model="m1",
        )
        provider1.api_key = "key1"
        provider1.enabled = True

        registry = MagicMock(spec=ProviderRegistry)
        registry.get_active_providers.return_value = [provider1]
        registry.count = 1

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API down")
        registry.get_client.return_value = mock_client

        messages = [{"role": "user", "content": "hello"}]
        with pytest.raises(RuntimeError, match="All providers failed"):
            multi_ai_chat(messages, registry)


# ============================================================
# OpenRouter Multi-Model Tests
# ============================================================

class TestOpenRouterDiscovery:
    def setup_method(self):
        reset_registry()

    def test_openrouter_multi_model_discovery(self):
        """OpenRouter key + multiple models creates virtual providers."""
        env = {
            "OPENROUTER_API_KEY": "sk-or-test123",
            "OPENROUTER_MODELS": "openai/gpt-4o,anthropic/claude-sonnet-4-5,google/gemini-2.5-flash",
        }
        with patch.dict(os.environ, env):
            reg = ProviderRegistry()
            active = reg.get_active_providers()
            names = [p.name for p in active]
            assert "openrouter:openai/gpt-4o" in names
            assert "openrouter:anthropic/claude-sonnet-4-5" in names
            assert "openrouter:google/gemini-2.5-flash" in names

    def test_openrouter_default_model_when_no_models_env(self):
        """Without OPENROUTER_MODELS, uses default model."""
        env = {"OPENROUTER_API_KEY": "sk-or-test123"}
        # Remove OPENROUTER_MODELS if present
        clean = {k: v for k, v in os.environ.items() if k != "OPENROUTER_MODELS"}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            reg = ProviderRegistry()
            names = [p.name for p in reg.get_active_providers()]
            assert "openrouter:openai/gpt-4o" in names

    def test_openrouter_no_key_skips(self):
        """Without OPENROUTER_API_KEY, no OpenRouter providers."""
        clean = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        with patch.dict(os.environ, clean, clear=True):
            reg = ProviderRegistry()
            names = [p.name for p in reg.get_active_providers()]
            openrouter_names = [n for n in names if n.startswith("openrouter:")]
            assert len(openrouter_names) == 0

    def test_openrouter_providers_have_correct_base_url(self):
        """All OpenRouter virtual providers use the OpenRouter base URL."""
        env = {
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_MODELS": "openai/gpt-4o,google/gemini-2.5-flash",
        }
        with patch.dict(os.environ, env):
            reg = ProviderRegistry()
            for p in reg.get_active_providers():
                if p.name.startswith("openrouter:"):
                    assert p.base_url == "https://openrouter.ai/api/v1"
                    assert p.api_key == "sk-or-test"

    def test_openrouter_model_set_correctly(self):
        """Each virtual provider has the correct model ID."""
        env = {
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_MODELS": "anthropic/claude-sonnet-4-5",
        }
        with patch.dict(os.environ, env):
            reg = ProviderRegistry()
            p = reg.get_provider("openrouter:anthropic/claude-sonnet-4-5")
            assert p is not None
            assert p.model == "anthropic/claude-sonnet-4-5"

    def test_openrouter_shared_client_cache(self):
        """All OpenRouter providers share a single cached client."""
        env = {
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_MODELS": "openai/gpt-4o,google/gemini-2.5-flash",
        }
        with patch.dict(os.environ, env):
            reg = ProviderRegistry()
            p1 = reg.get_provider("openrouter:openai/gpt-4o")
            p2 = reg.get_provider("openrouter:google/gemini-2.5-flash")
            client1 = reg.get_client(p1)
            client2 = reg.get_client(p2)
            # Same client object (shared cache)
            assert client1 is client2

    def test_openrouter_whitespace_in_models(self):
        """Whitespace in OPENROUTER_MODELS is trimmed."""
        env = {
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_MODELS": " openai/gpt-4o , google/gemini-2.5-flash , ",
        }
        with patch.dict(os.environ, env):
            reg = ProviderRegistry()
            names = [p.name for p in reg.get_active_providers()]
            assert "openrouter:openai/gpt-4o" in names
            assert "openrouter:google/gemini-2.5-flash" in names
            # No empty provider from trailing comma
            openrouter_count = len([n for n in names if n.startswith("openrouter:")])
            assert openrouter_count == 2

    def test_openrouter_coexists_with_direct_providers(self):
        """OpenRouter providers coexist with direct provider keys."""
        env = {
            "OPENAI_API_KEY": "sk-direct-openai",
            "OPENROUTER_API_KEY": "sk-or-test",
            "OPENROUTER_MODELS": "openai/gpt-4o",
        }
        with patch.dict(os.environ, env):
            reg = ProviderRegistry()
            names = [p.name for p in reg.get_active_providers()]
            # Both direct and OpenRouter version exist
            assert "chatgpt" in names
            assert "openrouter:openai/gpt-4o" in names


# ============================================================
# Token Tracking Tests
# ============================================================

class TestTokenTracking:
    """Tests for token usage tracking in ProviderResponse and ConsensusResult."""

    def test_provider_response_default_tokens_zero(self):
        """ProviderResponse should default token fields to 0."""
        resp = ProviderResponse(provider_name="test", content="hello", response_time_ms=100)
        assert resp.prompt_tokens == 0
        assert resp.completion_tokens == 0
        assert resp.total_tokens == 0

    def test_provider_response_with_tokens(self):
        """ProviderResponse should store token counts."""
        resp = ProviderResponse(
            provider_name="grok", content="response", response_time_ms=500,
            prompt_tokens=100, completion_tokens=50, total_tokens=150,
        )
        assert resp.prompt_tokens == 100
        assert resp.completion_tokens == 50
        assert resp.total_tokens == 150

    def test_consensus_result_default_empty_token_usage(self):
        """ConsensusResult should default token_usage to empty dict."""
        result = ConsensusResult(
            best_response="test", best_provider="grok", avg_score=85.0,
            all_scores={"grok": 85.0}, all_responses={"grok": "test"},
            generation_time_ms=1000, evaluation_time_ms=2000,
            total_time_ms=3000, providers_used=1, evaluation_rounds=0,
        )
        assert result.token_usage == {}

    def test_consensus_result_with_token_usage(self):
        """ConsensusResult should store per-provider token usage."""
        token_usage = {
            "grok": {"prompt": 500, "completion": 200, "total": 700},
            "gemini": {"prompt": 450, "completion": 180, "total": 630},
        }
        result = ConsensusResult(
            best_response="test", best_provider="grok", avg_score=85.0,
            all_scores={"grok": 85.0, "gemini": 80.0},
            all_responses={"grok": "test", "gemini": "test2"},
            generation_time_ms=1000, evaluation_time_ms=2000,
            total_time_ms=3000, providers_used=2, evaluation_rounds=2,
            token_usage=token_usage,
        )
        assert result.token_usage["grok"]["total"] == 700
        assert result.token_usage["gemini"]["prompt"] == 450

    def test_generate_single_captures_tokens(self):
        """_generate_single should extract token usage from API response."""
        from app.llm.multi_ai import _generate_single

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 120
        mock_usage.completion_tokens = 80
        mock_usage.total_tokens = 200

        mock_message = MagicMock()
        mock_message.content = "Test response content"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage = mock_usage

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        provider = AIProvider(
            name="test_provider",
            api_key_env="TEST_KEY",
            base_url="https://example.com/v1",
            default_model="test-model",
        )

        registry = MagicMock()
        registry.get_client.return_value = mock_client

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}):
            result = _generate_single(
                registry, provider,
                [{"role": "user", "content": "hello"}],
            )

        assert result.success is True
        assert result.prompt_tokens == 120
        assert result.completion_tokens == 80
        assert result.total_tokens == 200

    def test_generate_single_handles_none_usage(self):
        """_generate_single should gracefully handle None usage."""
        from app.llm.multi_ai import _generate_single

        mock_message = MagicMock()
        mock_message.content = "Response"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        provider = AIProvider(
            name="test_provider",
            api_key_env="TEST_KEY",
            base_url="https://example.com/v1",
            default_model="test-model",
        )

        registry = MagicMock()
        registry.get_client.return_value = mock_client

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}):
            result = _generate_single(
                registry, provider,
                [{"role": "user", "content": "hello"}],
            )

        assert result.success is True
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
        assert result.total_tokens == 0

    def test_multi_ai_chat_includes_token_usage(self):
        """multi_ai_chat should aggregate token usage from generation results."""
        def mock_gen_all(messages, registry, temperature, max_tokens, timeout=60):
            return [
                ProviderResponse("provA", "Response A", 500, True, "",
                                 prompt_tokens=100, completion_tokens=50, total_tokens=150),
                ProviderResponse("provB", "Response B", 600, True, "",
                                 prompt_tokens=120, completion_tokens=60, total_tokens=180),
            ]

        def mock_cross_eval(user_msg, responses, registry, system_context="", timeout=80):
            return [
                EvaluationResult("provA", {"provB": 80.0}),
                EvaluationResult("provB", {"provA": 90.0}),
            ]

        with patch("app.llm.multi_ai.generate_all", side_effect=mock_gen_all), \
             patch("app.llm.multi_ai.cross_evaluate", side_effect=mock_cross_eval):
            result = multi_ai_chat(
                [{"role": "user", "content": "test"}],
                registry=MagicMock(),
            )

        assert "provA" in result.token_usage
        assert "provB" in result.token_usage
        assert result.token_usage["provA"]["total"] == 150
        assert result.token_usage["provB"]["prompt"] == 120

    def test_multi_ai_chat_merges_all_system_messages_for_evaluation(self):
        captured = {}

        def mock_gen_all(messages, registry, temperature, max_tokens, timeout=60):
            return [
                ProviderResponse("provA", "Response A", 500, True, ""),
                ProviderResponse("provB", "Response B", 600, True, ""),
            ]

        def mock_cross_eval(user_msg, responses, registry, system_context="", timeout=80):
            captured["system_context"] = system_context
            return [
                EvaluationResult("provA", {"provB": 80.0}),
                EvaluationResult("provB", {"provA": 90.0}),
            ]

        with patch("app.llm.multi_ai.generate_all", side_effect=mock_gen_all), \
             patch("app.llm.multi_ai.cross_evaluate", side_effect=mock_cross_eval):
            multi_ai_chat(
                [
                    {"role": "system", "content": "base instructions"},
                    {"role": "system", "content": "rag context"},
                    {"role": "user", "content": "test"},
                ],
                registry=MagicMock(),
            )

        assert "base instructions" in captured["system_context"]
        assert "rag context" in captured["system_context"]


class TestCooldownBeforeEvaluation:
    def test_no_budget_returns_zero_when_almost_exhausted(self):
        with patch("app.llm.multi_ai.time.time", return_value=10.5):
            cooldown = _cooldown_before_evaluation(10.0, 0.0)

        assert cooldown == 0.0

    def test_budgeted_cooldown_scales_with_remaining_time(self):
        with patch("app.llm.multi_ai.time.time", return_value=2.0):
            cooldown = _cooldown_before_evaluation(20.0, 0.0)

        assert cooldown == pytest.approx(1.5)
