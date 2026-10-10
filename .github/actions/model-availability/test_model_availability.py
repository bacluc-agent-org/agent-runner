import json
import subprocess
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

import model_availability


CONFIG = {
    "provider": {
        "opencode-go-openai": {"options": {"baseURL": "https://opencode.ai/zen/go/v1"}},
        "opencode-go-anthropic": {"options": {"baseURL": "https://opencode.ai/zen/go/v1/messages"}},
        "openrouter": {"options": {"baseURL": "https://openrouter.ai/api/v1"}},
    }
}


@pytest.fixture(autouse=True)
def _provider_api_keys(monkeypatch):
    """Provider discovery tests exercise the real PROVIDERS loop; provide the keys."""
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-go-key")
    monkeypatch.setenv("OPENCODE_GO_2_API_KEY", "test-go-2-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")


def fake_run(args, *a, **kw):
    if args == ["opencode", "models"]:
        return types.SimpleNamespace(stdout="opencode/a-free\n")
    if args == ["opencode", "debug", "config"]:
        kw["stdout"].write(json.dumps(CONFIG))
        return types.SimpleNamespace(stdout="")
    raise AssertionError(f"unexpected args: {args}")


class TestDiscoverModels:
    def test_discovers_models_per_provider(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"id": "glm-5.2"}]}'

        def fake_urlopen(request, timeout=30):
            captured["headers"] = request.headers
            captured["full_url"] = request.full_url
            return FakeResponse()

        monkeypatch.setattr(model_availability.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        free_models, provider_models = model_availability.discover_models()
        assert free_models == ["opencode/a-free"]
        assert provider_models == {
            "opencode-go-openai": ["glm-5.2"],
            "opencode-go-anthropic": ["glm-5.2"],
            "openrouter": [],
        }
        assert captured["headers"]["User-agent"] == "curl/8.5.0"
        assert not any("Python-urllib" in v for v in captured["headers"].values())
        assert any(k.lower() == "x-opencode-session" for k in captured["headers"])
        assert captured["full_url"].endswith("/models")

    def test_endpoint_failure_per_provider(self, monkeypatch):
        def fail(request, timeout=30):
            raise RuntimeError("403 Forbidden")

        monkeypatch.setattr(model_availability.urllib.request, "urlopen", fail)
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        free_models, provider_models = model_availability.discover_models()
        assert free_models == ["opencode/a-free"]
        assert provider_models == {
            "opencode-go-openai": [],
            "opencode-go-anthropic": [],
            "openrouter": [],
        }

    def test_missing_baseurl_per_provider(self, monkeypatch):
        config = {
            "provider": {
                "opencode-go-openai": {"options": {"baseURL": "https://opencode.ai/zen/go/v1"}},
            }
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"id": "glm-5.2"}]}'

        def fake_run_missing(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout="opencode/a-free\n")
            if args == ["opencode", "debug", "config"]:
                kw["stdout"].write(json.dumps(config))
                return types.SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(
            model_availability.urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run_missing)
        free_models, provider_models = model_availability.discover_models()
        assert free_models == ["opencode/a-free"]
        assert provider_models == {"opencode-go-openai": ["glm-5.2"]}

    def test_config_read_failure(self, monkeypatch):
        def fail_config(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout="opencode/a-free\n")
            raise RuntimeError("opencode failed")

        monkeypatch.setattr(model_availability.subprocess, "run", fail_config)
        free_models, provider_models = model_availability.discover_models()
        assert free_models == ["opencode/a-free"]
        assert provider_models == {}

    def test_filters_openrouter_models_by_whitelist(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                if captured["full_url"].startswith("https://openrouter.ai"):
                    return b'{"data": [{"id": "deepseek/deepseek-chat-v3.1"}, {"id": "zai/GLM-4.5"}, {"id": "qwen/qwen3-8b"}, {"id": "anthropic/claude-sonnet-4.5"}, {"id": "cohere/north-mini-code:free"}]}'
                return b'{"data": [{"id": "deepseek/deepseek-chat-v3.1"}]}'

        def fake_urlopen(request, timeout=30):
            captured["full_url"] = request.full_url
            return FakeResponse()

        monkeypatch.setattr(model_availability.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        free_models, provider_models = model_availability.discover_models()
        assert provider_models["openrouter"] == ["cohere/north-mini-code:free"]
        assert provider_models["opencode-go-openai"] == ["deepseek/deepseek-chat-v3.1"]
        assert ("openrouter", "OPENROUTER_API_KEY") in model_availability.PROVIDERS

    def test_skips_provider_without_env_key(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"id": "glm-5.2"}]}'

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(
            model_availability.urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        free_models, provider_models = model_availability.discover_models()
        assert "openrouter" not in provider_models
        assert "opencode-go-openai" in provider_models

    def test_skips_provider_with_invalid_baseurl(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"id": "glm-5.2"}]}'

        config = {
            "provider": {
                "opencode-go-openai": {
                    "options": {
                        "baseURL": "https://opencode.ai/zen/go/v1",
                        "apiKey": "{env:OPENCODE_GO_API_KEY}",
                    }
                },
                "openrouter": {
                    "options": {
                        "baseURL": "/chat/completions",
                        "apiKey": "{env:OPENROUTER_API_KEY}",
                    }
                },
            }
        }

        def fake_run_invalid(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout="opencode/a-free\n")
            if args == ["opencode", "debug", "config"]:
                kw["stdout"].write(json.dumps(config))
                return types.SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(
            model_availability.urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run_invalid)
        free_models, provider_models = model_availability.discover_models()
        assert "openrouter" not in provider_models
        assert "opencode-go-openai" in provider_models

    def test_skips_provider_with_non_string_baseurl(self, monkeypatch):
        config = {
            "provider": {
                "openrouter": {
                    "options": {
                        "baseURL": ["https://openrouter.ai/api/v1"],
                        "apiKey": "{env:OPENROUTER_API_KEY}",
                    }
                }
            }
        }

        def fake_run_non_string(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout="opencode/a-free\n")
            if args == ["opencode", "debug", "config"]:
                kw["stdout"].write(json.dumps(config))
                return types.SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(model_availability.subprocess, "run", fake_run_non_string)
        free_models, provider_models = model_availability.discover_models()
        assert free_models == ["opencode/a-free"]
        assert provider_models == {}

    def test_skips_whitelisted_models_from_unprobeable_providers(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"id": "glm-5.2"}]}'

        config = {
            "provider": {
                "cortecs2": {"options": {"baseURL": "https://api.cortecs.ai/v1"}},
                "vshn-us-ai": {
                    "options": {
                        "baseURL": "{env:VSHN_US_BASE_URL}",
                        "apiKey": "{env:VSHN_US_AI_API_KEY}",
                    }
                },
                "opencode-go-openai": {
                    "options": {
                        "baseURL": "https://opencode.ai/zen/go/v1",
                        "apiKey": "{env:OPENCODE_GO_API_KEY}",
                    }
                },
            }
        }
        models_output = (
            "opencode/a-free\n"
            "cortecs2/glm-5.1\n"
            "vshn-us-ai/subscription.glm-5.2\n"
            "opencode-go-openai/glm-5.2\n"
        )

        def fake_run_filter(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout=models_output)
            if args == ["opencode", "debug", "config"]:
                kw["stdout"].write(json.dumps(config))
                return types.SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(
            model_availability.urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run_filter)
        free_models, provider_models = model_availability.discover_models()
        assert "cortecs2/glm-5.1" not in free_models
        assert "vshn-us-ai/subscription.glm-5.2" not in free_models
        assert "opencode-go-openai/glm-5.2" in free_models
        assert "opencode/a-free" in free_models

    def test_provider_with_resolved_api_key_is_probeable(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data": [{"id": "glm-5.2"}]}'

        config = {
            "provider": {
                "opencode-go-openai": {
                    "options": {
                        "baseURL": "https://opencode.ai/zen/go/v1",
                        "apiKey": "sk-resolved-key-value",
                    }
                },
                "cortecs2": {"options": {"baseURL": "https://api.cortecs.ai/v1"}},
            }
        }
        models_output = "opencode/a-free\nopencode-go-openai/glm-5.2\ncortecs2/glm-5.1\n"

        def fake_run_resolved(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout=models_output)
            if args == ["opencode", "debug", "config"]:
                kw["stdout"].write(json.dumps(config))
                return types.SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(
            model_availability.urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        monkeypatch.setattr(model_availability.subprocess, "run", fake_run_resolved)
        free_models, provider_models = model_availability.discover_models()
        assert "opencode-go-openai/glm-5.2" in free_models
        assert "cortecs2/glm-5.1" not in free_models


EXAMPLE_OPENCODE_WHITELIST = [r"(?:-|:)free$", r"big-pickle", r"glm", r"gpt-5\.6-luna", r"qwen", r"kimi"]

class TestProviderProbeable:
    @pytest.mark.parametrize("config", [{"openrouter": []}, {"openrouter": {"baseURL": []}}])
    def test_malformed_provider_config_is_not_probeable(self, config):
        assert not model_availability.provider_probeable("openrouter/foo:free", config, {})

    @pytest.mark.parametrize(
        "provider_config",
        [
            {"provider": {"openrouter": []}},
            {"provider": {"openrouter": {"options": []}}},
        ],
    )
    def test_malformed_discovered_provider_config_is_ignored(self, monkeypatch, provider_config):
        def fake_run(*args, **kwargs):
            kwargs["stdout"].write(json.dumps(provider_config))
            return types.SimpleNamespace(stdout="")

        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        assert model_availability.load_provider_config() == {}

    def test_resolved_key(self):
        config = {"openrouter": {"baseURL": "https://openrouter.ai/api/v1", "apiKey": "sk-or-xxx"}}
        assert model_availability.provider_probeable("openrouter/foo:free", config, {})

    def test_missing_key(self):
        config = {"cortecs2": {"baseURL": "https://api.cortecs.ai/v1", "apiKey": None}}
        assert not model_availability.provider_probeable("cortecs2/glm-5.1", config, {})

    def test_env_template(self):
        config = {
            "openrouter": {
                "baseURL": "https://openrouter.ai/api/v1",
                "apiKey": "{env:OPENROUTER_API_KEY}",
            }
        }
        assert model_availability.provider_probeable(
            "openrouter/foo:free", config, {"OPENROUTER_API_KEY": "k"}
        )
        assert not model_availability.provider_probeable("openrouter/foo:free", config, {})

    def test_unknown_provider_passes(self):
        assert model_availability.provider_probeable("opencode/a-free", {}, {})

    def test_invalid_baseurl(self):
        config = {"openrouter": {"baseURL": "/chat/completions", "apiKey": "sk-or-xxx"}}
        assert not model_availability.provider_probeable("openrouter/foo:free", config, {})

    def test_openai_passes_like_builtin_provider(self):
        assert model_availability.provider_probeable("openai/gpt-5.6-luna", {}, {})

class TestParseWhitelistedModels:
    def test_uses_exact_openai_patterns_without_changing_opencode_matching(self):
        output = (
            "openai/gpt-5.6-luna\n"
            "openai/gpt-5.3-codex-spark\n"
            "openai/gpt-5.6-luna-preview\n"
            "openai/gpt-5.3-codex-sparky\n"
            "openai/gpt-5.6-sol\n"
            "openai/gpt-5.6-sol-fast\n"
            "openai/gpt-5.6-terra\n"
            "openai/gpt-5.6-terra-fast\n"
            "openai/gpt-6-astra\n"
            "openai/gpt-6\n"
            "openai/gpt-6-astra-fast\n"
            "openai/gpt-6-luna\n"
            "openai/gpt-6-luna-fast\n"
            "openai/gpt-6-luna-preview\n"
            "opencode/gpt-5.6-luna-preview\n"
            "opencode/ling-3.0-flash-fin-free\n"
        )
        assert model_availability.parse_whitelisted_models(output) == [
            "opencode/ling-3.0-flash-fin-free",
            "openai/gpt-5.3-codex-spark",
            "openai/gpt-5.3-codex-sparky",
            "openai/gpt-5.6-luna",
            "openai/gpt-5.6-luna-preview",
            "openai/gpt-6-luna",
            "openai/gpt-6-luna-fast",
            "openai/gpt-6-luna-preview",
            "opencode/gpt-5.6-luna-preview",
        ]

    def test_extracts_free_models(self):
        output = (
            "opencode/big-pickle\n"
            "opencode/ling-3.0-flash-fin-free\n"
            "opencode/mimo-v2.5-free\n"
            "custom-provider/other-free\n"
            "custom-provider/big-pickle\n"
            "standalone-free\n"
            "big-pickle\n"
            "opencode/paid-model\n"
            "other/paid-model\n"
            "opencode-go-openai/glm-5.3\n"
            "opencode-go-openai/qwen3.8-flash\n"
            "opencode-go-openai/kimi-k3\n"
            "opencode-go-openai/gpt-5.6-luna\n"
            "openrouter/zai/GLM-4.5\n"
            "openrouter/deepseek/deepseek-chat-v3.1\n"
            "openrouter/anthropic/claude-sonnet-4.5\n"
            "openrouter/cohere/north-mini-code:free\n"
            "openrouter/google/gemma-4-31b-it:free\n"
        )
        assert model_availability.parse_whitelisted_models(output) == [
            "big-pickle",
            "custom-provider/big-pickle",
            "custom-provider/other-free",
            "opencode/big-pickle",
            "opencode/ling-3.0-flash-fin-free",
            "opencode/mimo-v2.5-free",
            "openrouter/cohere/north-mini-code:free",
            "openrouter/google/gemma-4-31b-it:free",
            "standalone-free",
            "opencode-go-openai/glm-5.3",
            "opencode-go-openai/gpt-5.6-luna",
            "opencode-go-openai/kimi-k3",
            "opencode-go-openai/qwen3.8-flash",
            "other/paid-model",
        ]

    def test_deduplicates(self):
        assert model_availability.parse_whitelisted_models(
            "opencode/a-free\nopencode/a-free\n",
        ) == ["opencode/a-free"]

    def test_free_first_then_whitelisted(self):
        assert model_availability.parse_whitelisted_models(
            "opencode/glm-5.3\nopencode/z-free\n",
        ) == ["opencode/z-free", "opencode/glm-5.3"]

    def test_case_insensitive_matching(self):
        assert model_availability.parse_whitelisted_models(
            "openrouter/cohere/north-mini-code:free\n",
        ) == ["openrouter/cohere/north-mini-code:free"]


class TestIsWhitelisted:
    def test_matches_case_insensitively(self):
        assert model_availability.is_whitelisted("zai/GLM-4.5", ["glm"])
        assert model_availability.is_whitelisted("qwen/qwen3-8b", ["qwen"])

    def test_no_match(self):
        patterns = EXAMPLE_OPENCODE_WHITELIST
        assert not model_availability.is_whitelisted("deepseek/deepseek-chat-v3.1", patterns)
        assert not model_availability.is_whitelisted("anthropic/claude-sonnet-4.5", patterns)

    def test_free_pattern_is_anchored(self):
        assert model_availability.is_whitelisted("a-free", ["(?:-|:)free$"])
        assert model_availability.is_whitelisted("a:free", ["(?:-|:)free$"])
        assert not model_availability.is_whitelisted("free", ["(?:-|:)free$"])

    def test_empty_patterns_match_nothing(self):
        assert not model_availability.is_whitelisted("anything", [])

    def test_workflow_referenced_models_match(self):
        patterns = EXAMPLE_OPENCODE_WHITELIST
        for model in [
            "opencode/big-pickle",
            "opencode-go-openai/qwen3.8-flash",
            "opencode-go-openai/glm-5.3",
            "opencode-go-openai/kimi-k3",
            "opencode-go-openai/gpt-5.6-luna",
        ]:
            assert model_availability.is_whitelisted(model, patterns)
        assert not model_availability.is_whitelisted("deepseek/deepseek-chat-v3.1", patterns)
        assert not model_availability.is_whitelisted("anthropic/claude-sonnet-4.5", patterns)

    def test_openrouter_free_models_match(self):
        patterns = model_availability.PROVIDER_WHITELISTS["openrouter"]
        for model in [
            "cohere/north-mini-code:free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3.5-lightning:free",
            "thinkingmachines/inkling:free",
        ]:
            assert model_availability.is_whitelisted(model, patterns)
        assert not model_availability.is_whitelisted("deepseek/deepseek-chat-v3.1", patterns)
        assert not model_availability.is_whitelisted("anthropic/claude-sonnet-4.5", patterns)


class TestParseGoModelIds:
    def test_extracts_and_deduplicates_ids(self):
        data = json.dumps(
            {"data": [{"id": "glm-5.2"}, {"id": "qwen3.8-flash"}, {"id": "glm-5.2"}]}
        )
        assert model_availability.parse_go_model_ids(data) == ["glm-5.2", "qwen3.8-flash"]

    def test_invalid_json_returns_empty(self):
        assert model_availability.parse_go_model_ids("not json") == []


class TestBuildCandidates:
    def test_skips_providers_without_api_key(self):
        env = {"OPENCODE_GO_API_KEY": "key1"}
        assert model_availability.build_candidates(
            ["opencode/a-free"],
            {"opencode-go-openai": ["glm-5.2"], "opencode-go-anthropic": ["glm-5.2"]},
            env,
        ) == [
            "opencode-go-openai/glm-5.2",
            "opencode-go-anthropic/glm-5.2",
            "opencode/a-free",
        ]

    def test_free_models_last(self):
        env = {"OPENCODE_GO_API_KEY": "key1", "OPENCODE_GO_2_API_KEY": "key2"}
        candidates = model_availability.build_candidates(
            ["opencode/a-free"],
            {
                "opencode-go-openai": ["glm-5.2"],
                "opencode-go-openai-2": ["glm-5.2"],
                "opencode-go-anthropic": ["glm-5.2"],
                "opencode-go-anthropic-2": ["glm-5.2"],
            },
            env,
        )
        assert candidates[-1] == "opencode/a-free"
        assert len(candidates) == 5

    def test_provider_models_are_not_crossed(self):
        env = {"OPENCODE_GO_API_KEY": "key1", "OPENCODE_GO_2_API_KEY": "key2"}
        provider_models = {
            "opencode-go-openai": ["x"],
            "opencode-go-openai-2": ["y"],
        }
        assert model_availability.build_candidates([], provider_models, env) == [
            "opencode-go-openai/x",
            "opencode-go-openai-2/y",
        ]

    def test_deduplicates_overlapping_models(self):
        env = {"OPENCODE_GO_API_KEY": "key1"}
        assert model_availability.build_candidates(
            ["opencode-go-openai/glm-5.2", "opencode/a-free"],
            {"opencode-go-openai": ["glm-5.2"]},
            env,
        ) == ["opencode-go-openai/glm-5.2", "opencode/a-free"]


class TestIsCacheFresh:
    def test_fresh_available(self):
        now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        entry = {"ok": True, "checked": "2026-09-06T10:00:00Z"}
        assert model_availability.is_cache_fresh(entry, now)

    def test_expired_available(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
        entry = {"ok": True, "checked": "2026-09-06T10:00:00Z"}
        assert not model_availability.is_cache_fresh(entry, now)

    def test_fresh_failed(self):
        now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        entry = {"ok": False, "checked": "2026-09-06T11:30:00Z"}
        assert model_availability.is_cache_fresh(entry, now)

    def test_expired_failed(self):
        now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
        entry = {"ok": False, "checked": "2026-09-05T11:00:00Z"}  # 28h gap > 24h TTL
        assert not model_availability.is_cache_fresh(entry, now)

    def test_exactly_ttl_is_not_fresh(self):
        now = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        entry = {"ok": True, "checked": "2026-09-06T10:00:00Z"}  # exactly 24h: 86400 < 86400 is False
        assert not model_availability.is_cache_fresh(entry, now)

    def test_missing_or_malformed_entry(self):
        now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        assert not model_availability.is_cache_fresh(None, now)
        assert not model_availability.is_cache_fresh({"ok": "yes"}, now)
        assert not model_availability.is_cache_fresh({"ok": True}, now)

    def test_failed_ttl_is_one_hour(self):
        # bacluc-agent/agent-todo#311: re-probe failures within the hour so a
        # wrongly-marked model recovers in half the time.
        assert model_availability.FAILED_TTL_HOURS == 1


class TestCandidatePriority:
    def test_big_pickle_first(self):
        assert model_availability.candidate_priority("opencode/big-pickle") == 0
        assert model_availability.candidate_priority("big-pickle") == 0

    def test_qwen_flash_second(self):
        assert model_availability.candidate_priority("opencode-go-openai/qwen3.8-flash") == 1
        assert model_availability.candidate_priority("opencode-go-openai-2/qwen3.8-flash") == 1

    def test_opencode_free_before_openrouter_free(self):
        assert model_availability.candidate_priority("opencode/a-free") == 2
        assert model_availability.candidate_priority("openrouter/zai/GLM-4.5:free") == 3

    def test_opencode_paid(self):
        assert model_availability.candidate_priority("opencode/paid-model") == 4

    def test_openai_models(self):
        assert model_availability.candidate_priority("openai/gpt-5.6-luna") == 4
        assert model_availability.candidate_priority("openai/gpt-6-luna") == 4
        assert model_availability.candidate_priority("openai/gpt-5.4") == 4

    def test_go_openai_providers(self):
        assert model_availability.candidate_priority("opencode-go-openai/glm-5.3") == 5
        assert model_availability.candidate_priority("opencode-go-openai-2/glm-5.3") == 5

    def test_go_openai_gpt_priority(self):
        assert model_availability.candidate_priority("opencode-go-openai/gpt-4") == 2
        assert model_availability.candidate_priority("opencode-go-openai-2/gpt-4") == 2
        assert model_availability.candidate_priority("opencode-go-openai/GPT-4") == 2
        assert model_availability.candidate_priority("opencode-go-openai/gpt-5.6-luna") == 2
        assert model_availability.candidate_priority("opencode-go-openai-2/gpt-4o") == 2

    def test_openrouter_gpt_priority(self):
        assert model_availability.candidate_priority("openrouter/openai/gpt-4o") == 6

    def test_go_openai_non_gpt_priority(self):
        assert model_availability.candidate_priority("opencode-go-openai/glm-5.3") == 5
        assert model_availability.candidate_priority("opencode-go-openai-2/glm-5.3") == 5

    def test_openai_non_gpt_priority(self):
        assert model_availability.candidate_priority("openai/other-model") == 4

    def test_go_anthropic_providers(self):
        assert model_availability.candidate_priority("opencode-go-anthropic/glm-5.3") == 6
        assert model_availability.candidate_priority("opencode-go-anthropic-2/glm-5.3") == 6

    def test_openrouter_paid_and_custom_provider_last(self):
        assert model_availability.candidate_priority("openrouter/anthropic/claude-sonnet-4.5") == 7
        assert model_availability.candidate_priority("custom-provider/other-free") == 7


class TestPrioritizeCandidates:
    def test_sorts_by_priority(self):
        candidates = [
            "openrouter/anthropic/claude-sonnet-4.5",
            "opencode-go-openai/qwen3.8-flash",
            "opencode/a-free",
            "opencode/big-pickle",
        ]
        assert model_availability.prioritize_candidates(candidates) == [
            "opencode/big-pickle",
            "opencode-go-openai/qwen3.8-flash",
            "opencode/a-free",
            "openrouter/anthropic/claude-sonnet-4.5",
        ]

    def test_stable_within_same_priority(self):
        candidates = ["opencode-go-openai/glm-5.3", "opencode-go-openai-2/kimi-k3"]
        assert model_availability.prioritize_candidates(candidates) == candidates


class TestSelectPending:
    def test_stale_highest_priority_first(self):
        now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
        cache = {
            "opencode/big-pickle": {"ok": False, "checked": "2026-09-05T11:00:00Z"},
            "opencode/a-free": {"ok": False, "checked": "2026-09-05T11:00:00Z"},
            "openrouter/anthropic/claude-sonnet-4.5": {"ok": False, "checked": "2026-09-05T11:00:00Z"},
        }
        pending, skipped = model_availability.select_pending(
            list(cache), cache, now
        )
        assert pending == [
            "opencode/big-pickle",
            "opencode/a-free",
            "openrouter/anthropic/claude-sonnet-4.5",
        ]
        assert skipped == 0

    def test_skips_fresh_entries(self):
        now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
        cache = {
            "opencode/big-pickle": {"ok": True, "checked": "2026-09-06T10:00:00Z"},
            "opencode/a-free": {"ok": False, "checked": "2026-09-05T11:00:00Z"},
        }
        pending, skipped = model_availability.select_pending(list(cache), cache, now)
        assert pending == ["opencode/a-free"]
        assert skipped == 0

    def test_all_fresh_returns_no_pending(self):
        now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        cache = {
            "opencode/big-pickle": {"ok": True, "checked": "2026-09-06T10:00:00Z"},
            "opencode/a-free": {"ok": False, "checked": "2026-09-06T11:30:00Z"},
        }
        pending, skipped = model_availability.select_pending(list(cache), cache, now)
        assert pending == []
        assert skipped == 0

    def test_missing_from_cache_is_pending(self):
        now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        cache = {"opencode/big-pickle": {"ok": True, "checked": "2026-09-06T10:00:00Z"}}
        pending, skipped = model_availability.select_pending(
            ["opencode/big-pickle", "opencode/a-free"], cache, now
        )
        assert pending == ["opencode/a-free"]
        assert skipped == 0

    def test_caps_at_probe_budget(self):
        now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
        stale = {
            f"opencode-go-openai/model-{i}": {"ok": False, "checked": "2026-09-05T11:00:00Z"}
            for i in range(model_availability.PROBE_BUDGET + 10)
        }
        pending, skipped = model_availability.select_pending(list(stale), stale, now)
        assert len(pending) == model_availability.PROBE_BUDGET
        assert skipped == 10


class TestProbeBudgetCoversCritical:
    def test_budget_covers_workflow_critical_models(self):
        assert model_availability.PROBE_BUDGET >= 28


class TestMergeResults:
    def test_merges_and_overwrites(self):
        cache = {"opencode/a-free": {"ok": True, "checked": "old"}}
        results = {"opencode/a-free": False, "opencode-go-openai/glm-5.2": True}
        assert model_availability.merge_results(cache, results, "2026-09-06T12:00:00Z") == {
            "opencode/a-free": {"ok": False, "checked": "2026-09-06T12:00:00Z"},
            "opencode-go-openai/glm-5.2": {"ok": True, "checked": "2026-09-06T12:00:00Z"},
        }


class TestAvailableModels:
    def test_free_first_then_paid(self):
        cache = {
            "opencode-go-openai/glm-5.2": {"ok": True, "checked": "x"},
            "opencode/a-free": {"ok": True, "checked": "x"},
            "opencode/b-free": {"ok": False, "checked": "x"},
            "opencode-go-openai-2/glm-5.2": {"ok": True, "checked": "x"},
        }
        assert model_availability.available_models(
            cache,
            ["opencode/a-free", "opencode/b-free"],
            {"opencode-go-openai": ["glm-5.2"], "opencode-go-openai-2": ["glm-5.2"]},
        ) == [
            "opencode/a-free",
            "opencode-go-openai/glm-5.2",
            "opencode-go-openai-2/glm-5.2",
        ]


class TestResolveCacheIssue:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("MODEL_AVAILABILITY_CACHE_ISSUE", "7")
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")
        calls = []

        def fake_run_gh(*args):
            calls.append(args)

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        assert model_availability.resolve_cache_issue() == "7"
        assert calls == []

    def test_auto_detect_by_title(self, monkeypatch):
        monkeypatch.delenv("MODEL_AVAILABILITY_CACHE_ISSUE", raising=False)
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")
        calls = []

        def fake_run_gh(*args):
            calls.append(args)
            return "3\n"

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        assert model_availability.resolve_cache_issue() == "3"
        assert calls == [
            (
                "api",
                "search/issues?q=repo:bacluc-agent/agent-todo+is:issue+in:title+%22model-discovery+cache%22",
                "--jq",
                ".items[0].number // empty",
            )
        ]

    def test_auto_detect_when_env_empty_string(self, monkeypatch):
        monkeypatch.setenv("MODEL_AVAILABILITY_CACHE_ISSUE", "")
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")
        calls = []

        def fake_run_gh(*args):
            calls.append(args)
            return "3\n"

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        assert model_availability.resolve_cache_issue() == "3"
        assert calls == [
            (
                "api",
                "search/issues?q=repo:bacluc-agent/agent-todo+is:issue+in:title+%22model-discovery+cache%22",
                "--jq",
                ".items[0].number // empty",
            )
        ]

    def test_no_repo_returns_none(self, monkeypatch, capsys):
        monkeypatch.delenv("MODEL_AVAILABILITY_CACHE_ISSUE", raising=False)
        monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
        assert model_availability.resolve_cache_issue() is None
        assert "warning: ISSUE_REPOSITORY not set" in capsys.readouterr().err

    def test_search_failure_returns_none(self, monkeypatch, capsys):
        monkeypatch.delenv("MODEL_AVAILABILITY_CACHE_ISSUE", raising=False)
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")

        def fail(*args):
            raise RuntimeError("gh failed")

        monkeypatch.setattr(model_availability, "run_gh", fail)
        assert model_availability.resolve_cache_issue() is None
        assert "warning: failed to auto-detect the cache issue" in capsys.readouterr().err


class TestReadCache:
    def test_reads_issue_body(self, monkeypatch):
        monkeypatch.setattr(
            model_availability,
            "run_gh",
            lambda *args: '{"opencode/a-free": {"ok": true, "checked": "x"}}',
        )
        assert model_availability.read_cache("49") == {
            "opencode/a-free": {"ok": True, "checked": "x"}
        }

    def test_returns_empty_on_failure(self, monkeypatch):
        def fail(*args):
            raise RuntimeError("gh failed")

        monkeypatch.setattr(model_availability, "run_gh", fail)
        assert model_availability.read_cache("49") == {}


class TestWriteCache:
    def test_writes_issue_body(self, monkeypatch):
        monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
        calls = []

        def fake_run_gh(*args):
            calls.append(args)

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        model_availability.write_cache("49", {"a": 1})
        assert calls == [("issue", "edit", "49", "--body", '{"a": 1}')]

    def test_warns_on_failure(self, monkeypatch, capsys):
        def fail(*args):
            raise RuntimeError("gh failed")

        monkeypatch.setattr(model_availability, "run_gh", fail)
        model_availability.write_cache("49", {"a": 1})
        assert "warning: failed to write cache issue 49" in capsys.readouterr().err

    def test_warns_when_body_exceeds_limit(self, monkeypatch, capsys):
        calls = []

        def fake_run_gh(*args):
            calls.append(args)

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        model_availability.write_cache("49", {"x": "a" * 70000})
        assert calls == []
        err = capsys.readouterr().err
        assert "warning: cache body is" in err
        assert str(model_availability.GITHUB_ISSUE_BODY_LIMIT) in err

    def test_warns_with_gh_stderr(self, monkeypatch, capsys):
        def fail(*args):
            raise subprocess.CalledProcessError(1, args, "", "gh: not authenticated (add-mask)")

        monkeypatch.setattr(model_availability, "run_gh", fail)
        model_availability.write_cache("49", {"a": 1})
        assert "gh: not authenticated (add-mask)" in capsys.readouterr().err


class TestModelsEndpointFor:
    def test_openai_style(self):
        assert (
            model_availability.models_endpoint_for("https://opencode.ai/zen/go/v1")
            == "https://opencode.ai/zen/go/v1/models"
        )

    def test_anthropic_style(self):
        assert (
            model_availability.models_endpoint_for("https://opencode.ai/zen/go/v1/messages")
            == "https://opencode.ai/zen/go/v1/models"
        )

    def test_trailing_slash(self):
        assert (
            model_availability.models_endpoint_for("https://opencode.ai/zen/go/v1/")
            == "https://opencode.ai/zen/go/v1/models"
        )
        assert (
            model_availability.models_endpoint_for("https://opencode.ai/zen/go/v1/messages/")
            == "https://opencode.ai/zen/go/v1/models"
        )


class TestLoadProviderBaseUrls:
    def test_returns_base_urls(self, monkeypatch):
        config = {
            "provider": {
                "opencode-go-openai": {"options": {"baseURL": "https://opencode.ai/zen/go/v1"}},
                "opencode-go-anthropic": {"options": {"baseURL": "https://opencode.ai/zen/go/v1/messages"}},
                "no-base-url": {"options": {}},
            }
        }
        def fake_run(*args, **kwargs):
            kwargs["stdout"].write(json.dumps(config))
            return types.SimpleNamespace(stdout="")

        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        assert model_availability.load_provider_base_urls() == {
            "opencode-go-openai": "https://opencode.ai/zen/go/v1",
            "opencode-go-anthropic": "https://opencode.ai/zen/go/v1/messages",
        }

    def test_returns_empty_on_failure(self, monkeypatch):
        def fail(*args, **kwargs):
            raise RuntimeError("opencode failed")

        monkeypatch.setattr(model_availability.subprocess, "run", fail)
        assert model_availability.load_provider_base_urls() == {}


class TestProbeModelLogging:
    def test_writes_probe_log_with_stdout_and_stderr(self, tmp_path, monkeypatch):
        class FakeResult:
            returncode = 0
            stdout = "OK\n"
            stderr = "debug: loaded model\n"

        monkeypatch.setattr(
            model_availability.subprocess, "run", lambda *args, **kwargs: FakeResult()
        )
        assert (
            model_availability.probe_model("opencode-go-openai/glm-5.2", str(tmp_path))
            is True
        )
        log = tmp_path / "model-probes" / "probe-opencode-go-openai-glm-5.2.log"
        assert log.read_text() == "OK\ndebug: loaded model\n"

    def test_writes_probe_log_even_when_probe_fails(self, tmp_path, monkeypatch):
        class FakeResult:
            returncode = 1
            stdout = "model unavailable\n"
            stderr = ""

        monkeypatch.setattr(
            model_availability.subprocess, "run", lambda *args, **kwargs: FakeResult()
        )
        assert model_availability.probe_model("opencode/a-free", str(tmp_path)) is False
        log = tmp_path / "model-probes" / "probe-opencode-a-free.log"
        assert log.read_text() == "model unavailable\n"

    def test_writes_timeout_marker(self, tmp_path, monkeypatch):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

        monkeypatch.setattr(model_availability.subprocess, "run", timeout)
        assert model_availability.probe_model("opencode/a-free", str(tmp_path)) is False
        log = tmp_path / "model-probes" / "probe-opencode-a-free.log"
        assert log.read_text() == "TIMEOUT after 30s\n"


class TestDiscoverModelsLogging:
    def test_writes_opencode_models_log(self, tmp_path, monkeypatch):
        class FakeResult:
            stdout = "opencode/a-free\nopencode/big-pickle\n"
            stderr = ""

        class FakeResponse:
            def read(self):
                return b'{"data": []}'

        def fake_run_logging(args, *a, **kw):
            if args == ["opencode", "models"]:
                return types.SimpleNamespace(stdout=FakeResult.stdout, stderr=FakeResult.stderr)
            if args == ["opencode", "debug", "config"]:
                kw["stdout"].write(json.dumps(CONFIG))
                return types.SimpleNamespace(stdout="")
            raise AssertionError(f"unexpected args: {args}")

        monkeypatch.setattr(model_availability.subprocess, "run", fake_run_logging)
        monkeypatch.setattr(
            model_availability.urllib.request,
            "urlopen",
            lambda *args, **kwargs: FakeResponse(),
        )
        monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
        free_models, provider_models = model_availability.discover_models()
        assert free_models == ["opencode/a-free", "opencode/big-pickle"]
        assert provider_models == {
            "opencode-go-openai": [],
            "opencode-go-anthropic": [],
            "openrouter": [],
        }
        log = tmp_path / "model-probes" / "opencode-models.log"
        assert log.read_text() == "opencode/a-free\nopencode/big-pickle\n"


class TestRunGhRepoFlag:
    def test_run_gh_passes_args_through(self, monkeypatch):
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")
        captured = {}

        def fake_run(args, *a, **kw):
            captured["args"] = args
            return types.SimpleNamespace(stdout="")

        monkeypatch.setattr(model_availability.subprocess, "run", fake_run)
        model_availability.run_gh("issue", "view", "49")
        assert captured["args"] == ["gh", "issue", "view", "49"]

    def test_read_cache_passes_repo_flag_after_subcommand(self, monkeypatch):
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")
        captured = {}

        def fake_run_gh(*args):
            captured["args"] = args
            return "{}"

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        model_availability.read_cache("49")
        assert captured["args"] == (
            "issue",
            "view",
            "49",
            "-R",
            "bacluc-agent/agent-todo",
            "--json",
            "body",
            "--jq",
            ".body",
        )

    def test_write_cache_passes_repo_flag_after_subcommand(self, monkeypatch):
        monkeypatch.setenv("ISSUE_REPOSITORY", "bacluc-agent/agent-todo")
        captured = {}

        def fake_run_gh(*args):
            captured["args"] = args

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        model_availability.write_cache("49", {"a": 1})
        assert captured["args"] == (
            "issue",
            "edit",
            "49",
            "-R",
            "bacluc-agent/agent-todo",
            "--body",
            '{"a": 1}',
        )

    def test_no_repo_flag_when_unset(self, monkeypatch):
        monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
        captured = []

        def fake_run_gh(*args):
            captured.append(args)
            return "{}"

        monkeypatch.setattr(model_availability, "run_gh", fake_run_gh)
        model_availability.read_cache("49")
        model_availability.write_cache("49", {"a": 1})
        assert all("-R" not in args for args in captured)


class TestOpencodeWhitelist:
    def test_openrouter_whitelist_is_free_only(self):
        patterns = model_availability.PROVIDER_WHITELISTS["openrouter"]
        assert patterns == [r"(?:-|:)free$", r"big-pickle"]
        assert model_availability.is_whitelisted("openrouter/thinkingmachines/inkling:free", patterns)
        assert model_availability.is_whitelisted("openrouter/big-pickle", patterns)
        assert not model_availability.is_whitelisted("openrouter/moonshotai/kimi-k2", patterns)
        assert not model_availability.is_whitelisted("openrouter/qwen/qwen3-max", patterns)
        assert not model_availability.is_whitelisted("openrouter/zai/GLM-4.5", patterns)

    def test_free_and_whitelisted_models_pass(self):
        patterns = model_availability.PROVIDER_WHITELISTS["opencode"]
        for model in ["opencode/a-free", "opencode/big-pickle", "opencode/qwen3.8-flash"]:
            assert model_availability.is_whitelisted(model, patterns)
        assert not model_availability.is_whitelisted("opencode/some-paid-model", patterns)

    def test_openai_whitelist_allows_gpt_6_luna_blocks_other_gpt_6(self):
        patterns = model_availability.PROVIDER_WHITELISTS["openai"]
        for model in [
            "gpt-5.3-codex-spark",
            "gpt-5.4",
            "gpt-5.4-fast",
            "gpt-5.4-mini",
            "gpt-5.4-mini-fast",
            "gpt-5.5",
            "gpt-5.5-fast",
            "gpt-5.6-luna",
            "gpt-5.6-luna-fast",
            "gpt-5.6-luna-preview",
            "gpt-6-luna",
            "gpt-6-luna-fast",
            "gpt-6-luna-preview",
            "gpt-4o",
        ]:
            assert model_availability.is_whitelisted(model, patterns)
        for model in [
            "gpt-5.6-sol",
            "gpt-5.6-sol-fast",
            "gpt-5.6-sol-preview",
            "gpt-5.6-terra",
            "gpt-5.6-terra-fast",
            "gpt-5.6-terra-preview",
            "gpt-6",
            "gpt-6-astra",
            "gpt-6-astra-fast",
            "gpt-6-sol",
        ]:
            assert not model_availability.is_whitelisted(model, patterns)


class TestModelAvailabilityAction:
    def test_auth_materialized_by_setup_opencode_not_here(self):
        root = Path(__file__).parents[3]
        setup = (root / ".github/actions/setup-opencode/action.yml").read_text()
        assert "umask 077" in setup
        assert (
            "printf '%s' \"$OPENCODE_AUTH_CONTENT\" > ~/.local/share/opencode/auth.json"
            in setup
        )
        assert "printf '%s\\n' \"$OPENCODE_AUTH_CONTENT\"" not in setup
        action = Path(__file__).with_name("action.yml").read_text()
        assert "OPENCODE_AUTH_CONTENT" not in action
        assert "auth.json" not in action
        assert "python3" in action


class TestMainProbeSummary:
    def test_prints_fresh_cache_summary_without_probing(self, monkeypatch, capsys):
        monkeypatch.setattr(model_availability, "resolve_cache_issue", lambda: None)
        monkeypatch.setattr(
            model_availability, "discover_models", lambda: (["opencode/a-free"], {})
        )
        monkeypatch.setattr(
            model_availability, "build_candidates", lambda *args: ["opencode/a-free"]
        )
        monkeypatch.setattr(
            model_availability, "select_pending", lambda *args: ([], 0)
        )
        def fail_if_probed(*args):
            raise AssertionError("fresh candidates must not be probed")

        monkeypatch.setattr(model_availability, "probe_candidates", fail_if_probed)
        monkeypatch.setattr(model_availability, "write_outputs", lambda *args: None)
        assert model_availability.main() == 0
        assert "probe results: 0 checked, 0 ok, 0 failed (1 candidates fresh)" in capsys.readouterr().out

    def test_does_not_invoke_probe_for_empty_pending(self, monkeypatch):
        monkeypatch.setattr(model_availability, "resolve_cache_issue", lambda: None)
        monkeypatch.setattr(model_availability, "discover_models", lambda: ([], {}))
        monkeypatch.setattr(model_availability, "build_candidates", lambda *args: [])
        monkeypatch.setattr(model_availability, "select_pending", lambda *args: ([], 0))
        calls = []
        monkeypatch.setattr(model_availability, "probe_candidates", lambda *args: calls.append(args))
        monkeypatch.setattr(model_availability, "write_outputs", lambda *args: None)
        assert model_availability.main() == 0
        assert calls == []

    def test_prints_checked_probe_summary(self, monkeypatch, capsys):
        monkeypatch.setattr(model_availability, "resolve_cache_issue", lambda: None)
        monkeypatch.setattr(
            model_availability, "discover_models", lambda: (["opencode/a-free", "opencode/b-free"], {})
        )
        monkeypatch.setattr(
            model_availability, "build_candidates", lambda *args: ["opencode/a-free", "opencode/b-free"]
        )
        monkeypatch.setattr(
            model_availability,
            "select_pending",
            lambda *args: (["opencode/a-free", "opencode/b-free"], 0),
        )
        monkeypatch.setattr(
            model_availability,
            "probe_candidates",
            lambda *args: {"opencode/a-free": True, "opencode/b-free": False},
        )
        monkeypatch.setattr(model_availability, "write_outputs", lambda *args: None)
        assert model_availability.main() == 0
        assert "probe results: 2 checked, 1 ok, 1 failed" in capsys.readouterr().out


class TestDiscoveryTimeout:
    def test_discovery_timeout_is_bounded(self):
        assert model_availability.DISCOVERY_TIMEOUT_SECONDS <= 120


class TestRefineIssuesExitCode:
    WORKFLOW = Path(__file__).parents[3] / ".github/workflows/refine-issues.yml"

    @classmethod
    def _completion_tail(cls):
        lines = cls.WORKFLOW.read_text().splitlines()
        index = next(
            i
            for i, line in enumerate(lines)
            if line.strip().startswith("printf '\\nRefinement complete:")
        )
        return "\n".join(lines[index : index + 2])

    @staticmethod
    def _run(tail, refined, failed):
        return subprocess.run(
            ["bash", "-c", f"refined={refined}\nfailed={failed}\n{tail}"],
            capture_output=True,
            text=True,
        )

    def test_all_refinements_failed_fails_the_step(self):
        result = self._run(self._completion_tail(), 0, 3)
        assert result.returncode == 1
        assert "0 refined, 3 failed" in result.stdout

    def test_partial_success_still_succeeds(self):
        assert self._run(self._completion_tail(), 1, 2).returncode == 0


class TestRefineIssuesNoCandidates:
    WORKFLOW = Path(__file__).parents[3] / ".github/workflows/refine-issues.yml"

    @classmethod
    def _no_candidates_block(cls):
        lines = cls.WORKFLOW.read_text().splitlines()
        start = next(
            i
            for i, line in enumerate(lines)
            if line.strip() == 'if [[ ! -s "$candidates" ]]; then'
        )
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "fi")
        return "\n".join(lines[start : end + 1])

    def test_no_candidates_exits_zero_before_the_guard(self, tmp_path):
        search_json = tmp_path / "candidates.json"
        search_json.write_text("[]")
        candidates = tmp_path / "candidate-numbers.txt"
        block = self._no_candidates_block()
        script = (
            f"search_json={str(search_json)!r}\n"
            f"candidates={str(candidates)!r}\n"
            "jq -r '.[].number | tostring' \"$search_json\" > \"$candidates\"\n"
            f"{block}\n"
            # canary: the block's own `exit 0` must stop the script before the guard
            "printf 'GUARD-REACHED\\n'\n"
        )
        result = subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", script],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "No unrefined open issues; nothing to do." in result.stdout
        assert "GUARD-REACHED" not in result.stdout

    def test_no_candidates_exit_precedes_the_guard(self):
        content = self.WORKFLOW.read_text()
        assert content.index("No unrefined open issues; nothing to do.") < content.index(
            "(( refined > 0 )) || exit 1"
        )

    def test_refinement_candidate_filtering_preserves_unrefined_first_and_skips_their_comments(self, tmp_path):
        content = self.WORKFLOW.read_text()
        assert 'is:open+is:issue+label:agent-refined+-label:ready-for-implementation+-label:agent-ignore+sort:updated-desc&per_page=10' in content
        start = content.index('          candidates="$RUNNER_TEMP/candidate-numbers.txt"')
        end = content.index('          if [[ ! -s "$candidates" ]]; then', start)
        block = content[start:end]
        assert block.count('while IFS= read -r number; do') == 1
        fake_gh = tmp_path / "gh"
        fake_gh.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[0] == "api" and any(arg.startswith("search/issues?") for arg in args):
    query = next(arg for arg in args if arg.startswith("search/issues?"))
    issues = [11] if "sort:created-asc" in query else [22, 33]
    print(json.dumps([{"number": number} for number in issues]))
elif args[0] == "api":
    endpoint = next(arg for arg in args if arg.startswith("repos/"))
    number = int(endpoint.split("/")[4])
    comments = {22: [{"user":{"type":"User"},"body":"new feedback","created_at":"2026-02-02T00:00:00Z"}], 33: [{"user":{"type":"Bot"},"body":"bot","created_at":"2026-02-03T00:00:00Z"},{"user":{"type":"User"},"body":"<!-- agent-progress -->","created_at":"2026-02-04T00:00:00Z"},{"user":{"type":"User"},"body":"old","created_at":"2026-02-01T00:00:00Z"}]}
    print(json.dumps([comments.get(number, [])]))
elif args[0] == "issue":
    print("2026-02-01T00:00:00Z" if args[2] == "22" else "2026-02-05T00:00:00Z")
''')
        fake_gh.chmod(0o755)
        result = subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", f"ISSUE_REPOSITORY=example/issues\nRUNNER_TEMP={str(tmp_path)!r}\nPATH={str(tmp_path)!r}:$PATH\n{block}cat \"$candidates\""],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.rstrip().splitlines() == ["11", "22"]
        assert len(list(tmp_path.glob("all-comments-*.json"))) == 2

    def test_refined_issue_filter_excludes_bots_progress_and_old_comments(self):
        content = self.WORKFLOW.read_text()
        assert content.count('gh api --paginate --slurp "repos/$ISSUE_REPOSITORY/issues/$number/comments?per_page=100"') == 1
        assert 'select(.user.type != "Bot")' in content
        assert 'contains("<!-- agent-progress -->") | not' in content
        assert "select(.created_at > $refined_at)" in content

    def test_refinement_marker_is_written_after_validation_and_removed_from_prompt(self):
        content = self.WORKFLOW.read_text()
        validation = content.index("if (( validation_status == 0 )); then")
        marker_write = content.index("printf '\\n\\n<!-- agent-refined: %s -->\\n'")
        issue_edit = content.index('gh issue edit "$number" --body-file "$out"')
        prompt_body = content[content.index('body="$(gh issue view'):content.index('prompt="$RUNNER_TEMP/refine-prompt.txt"')]
        assert validation < marker_write < issue_edit
        assert "sed '/<!-- agent-refined: [^ ]* -->/d'" in prompt_body
        assert "This is a prior refinement; the newest comments are new feedback" in content


class TestWorkflowOpenRouterSelection:
    WORKFLOWS = [
        ".github/workflows/opencode.yml",
        ".github/workflows/hourly-issue.yml",
        ".github/workflows/refine-issues.yml",
    ]

    def test_workflows_select_openrouter_and_exclude_weak_free_models(self):
        for path in self.WORKFLOWS:
            content = Path(path).read_text()
            assert "openrouter/*)" in content
            assert '[[ -n "$OPENROUTER_API_KEY" ]]' in content
            assert "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in content
            assert "model-deny-list.txt" in content

    def test_last_resort_is_deny_list_filtered_and_fails_closed(self):
        for path in self.WORKFLOWS:
            content = Path(path).read_text()
            mapfile = content.index("mapfile -t free_models")
            give_up = content.rindex("not dispatching.' >&2")
            filtered = min(
                index
                for index in (
                    content.find(
                        'grep -Ev -f scripts/model-deny-list.txt "$available_models_file"',
                        mapfile,
                    ),
                    content.find(
                        'grep -Ev -f "$RUNNER_TEMP/agent-runner/scripts/model-deny-list.txt" "$available_models_file"',
                        mapfile,
                    ),
                )
                if index >= 0
            )
            assert mapfile < filtered < give_up
            assert 'head -n1 "$available_models_file"' not in content
            assert "last resort (deny-listed)" not in content

    def test_empty_filtered_candidates_exit_before_dispatch(self, tmp_path):
        deny_list = tmp_path / "deny-list.txt"
        deny_list.write_text("bad-model\n")
        available = tmp_path / "available-models.txt"
        available.write_text("bad-model\n")
        result = subprocess.run(
            [
                "bash",
                "-Eeuo",
                "pipefail",
                "-c",
                """
                selection_model="$(grep -Ev -f "$1" "$2" | head -n1 || true)"
                if [[ -z "$selection_model" ]]; then
                  printf '%s\n' 'No model is available; not dispatching.' >&2
                  exit 1
                fi
                """,
                "bash",
                str(deny_list),
                str(available),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "not dispatching" in result.stderr

    def test_opencode_rejects_deny_list_model_override(self):
        content = Path(".github/workflows/opencode.yml").read_text()
        assert 'grep -Eq "$deny_re" <<<"$requested"' in content
        assert "Requested model is deny-listed; not dispatching." in content

    def test_deny_list_file_exists(self):
        root = Path(__file__).parents[3]
        deny_list = root / "scripts/model-deny-list.txt"
        assert deny_list.is_file()
        lines = deny_list.read_text().splitlines()
        assert lines, "deny-list must not be empty"
        for line in lines:
            assert line.strip(), "deny-list must not contain blank lines"
            assert "|" not in line, (
                "grep -f reads every line as a live pattern; comments must stay pipe-free"
            )

    def test_deny_list_excludes_known_weak_models_and_keeps_big_pickle(self):
        root = Path(__file__).parents[3]
        deny_list = root / "scripts/model-deny-list.txt"
        weak_models = [
            "opencode/nemotron-3-ultra-free",
            "opencode/muse-spark-1.3-contributor-free",
            "opencode/ling-3.0-flash-fin-free",
            "opencode/mimo-v2.5-free",
            "openrouter/cohere/north-mini-code:free",
            "openrouter/poolside/laguna-xs-2.1:free",
            "openrouter/thinkingmachines/inkling:free",
            "openrouter/dots-studio/dots-3-note-preview:free",
            "openrouter/nex-agi/nex-n2.5-pro:free",
            "openrouter/liquid/lfm-2.5:free",
            "openrouter/inclusionai/ling-3.0-flash-sante:free",
        ]
        for model in weak_models:
            result = subprocess.run(
                ["grep", "-Ev", "-f", str(deny_list)],
                input=model,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1, f"{model} must be deny-listed"
        kept = subprocess.run(
            ["grep", "-Ev", "-f", str(deny_list)],
            input="opencode/big-pickle",
            capture_output=True,
            text=True,
        )
        assert kept.returncode == 0, "opencode/big-pickle must be kept"
