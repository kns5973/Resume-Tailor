import requests
import pytest

from resume_tailor.llm import (
    GroqClient,
    LLMError,
    MockLLMClient,
    _parse_json,
    get_client,
    model_name_for,
)


def test_model_name_for_tiering():
    assert model_name_for("jd_parser") == "llama-3.1-8b-instant"  # fast tier
    assert model_name_for("matcher_reformulation") == "llama-3.3-70b-versatile"  # strong tier
    assert model_name_for("unknown_task") == "llama-3.3-70b-versatile"  # safer default


def test_model_name_env_override(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL_FAST", "qwen/qwen3.6-27b")
    assert model_name_for("jd_parser") == "qwen/qwen3.6-27b"


def test_parse_json_strips_fences():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('{"a": 1}') == {"a": 1}
    with pytest.raises(LLMError):
        _parse_json("not json at all")


def test_parse_json_tolerates_prose_around_object():
    assert _parse_json('Here is the parsed JD:\n{"title": "Engineer"}\nHope this helps') == {"title": "Engineer"}


def test_mock_client_records_calls_and_reuses_last():
    client = MockLLMClient()
    data = client.complete_json(task="jd_parser", system="s", prompt="p")
    assert data["title"] == "Senior Backend Engineer"
    client.complete_json(task="jd_parser", system="s", prompt="p")
    assert len(client.calls) == 2
    assert client.calls[0][0] == "jd_parser"


def test_mock_client_register():
    client = MockLLMClient()
    client.register("matcher_reformulation", ['{"queries": ["redis"]}'])
    assert client.complete_json(task="matcher_reformulation", system="s", prompt="p") == {"queries": ["redis"]}


def test_mock_client_missing_task_raises():
    client = MockLLMClient({"some_task": ['{"ok": true}']})
    with pytest.raises(LLMError):
        client.complete_json(task="unregistered_task", system="s", prompt="p")


def test_reformulation_has_no_generic_default():
    # a generic reformulation would silently steer every requirement to the
    # same chunks in dry-run mode — it must fail loudly instead
    with pytest.raises(LLMError):
        MockLLMClient().complete_json(task="matcher_reformulation", system="s", prompt="p")


def test_mock_client_callable_handler():
    def handler(prompt: str):
        return '{"queries": ["landscaping"]}' if "lawn" in prompt else '{"queries": ["redis"]}'

    client = MockLLMClient({"matcher_reformulation": handler})
    assert client.complete_json(task="matcher_reformulation", system="s", prompt="lawn care") == {"queries": ["landscaping"]}
    assert client.complete_json(task="matcher_reformulation", system="s", prompt="redis job") == {"queries": ["redis"]}
    assert len(client.calls) == 2


def test_mock_client_scripted_sequence():
    client = MockLLMClient({"jd_parser": ["not json", '{"title": "Retry"}']})
    with pytest.raises(LLMError):
        client.complete_json(task="jd_parser", system="s", prompt="p")
    data = client.complete_json(task="jd_parser", system="s", prompt="p")
    assert data == {"title": "Retry"}


# --------------------------------------------------------------------------
# GroqClient (network mocked)
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


def test_groq_client_parses_response(monkeypatch):
    content = '{"title": "Senior Backend Engineer", "requirements": []}'
    fake = _FakeResponse(200, payload={"choices": [{"message": {"content": content}}]})

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return fake

    monkeypatch.setattr("requests.post", fake_post)

    client = GroqClient(api_key="gsk-test")
    data = client.complete_json(task="jd_parser", system="s", prompt="p")
    assert data["title"] == "Senior Backend Engineer"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer gsk-test"
    assert captured["json"]["model"] == "llama-3.1-8b-instant"  # fast tier for jd_parser


def test_groq_client_max_tokens_per_task(monkeypatch):
    """Output budget scales with task: small for parsing, large for the builder."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, payload={"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("requests.post", fake_post)
    client = GroqClient(api_key="gsk-test")
    client.complete_json(task="jd_parser", system="s", prompt="p")
    assert captured["json"]["max_tokens"] == 1024  # short structured output
    client.complete_json(task="bullet_generation", system="s", prompt="p")
    assert captured["json"]["max_tokens"] == 4096  # full resume draft


def test_max_tokens_env_override(monkeypatch):
    from resume_tailor.config import max_tokens_for

    monkeypatch.setenv("GROQ_MAX_TOKENS_BULLET_GENERATION", "8000")
    assert max_tokens_for("bullet_generation") == 8000
    assert max_tokens_for("jd_parser") == 1024


def test_groq_client_http_error_raises_llm_error(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeResponse(429, text="rate limited"))
    client = GroqClient(api_key="gsk-test", retry_backoff=0)
    with pytest.raises(LLMError, match="429"):
        client.complete_json(task="jd_parser", system="s", prompt="p")


def test_groq_client_retries_429_then_succeeds(monkeypatch):
    """Transient 429s are retried (bounded); a later 200 is used."""
    content = '{"title": "Engineer", "requirements": []}'
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            return _FakeResponse(429, text="rate limited")
        return _FakeResponse(200, payload={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr("requests.post", fake_post)
    client = GroqClient(api_key="gsk-test", retry_backoff=0)
    data = client.complete_json(task="jd_parser", system="s", prompt="p")
    assert data["title"] == "Engineer"
    assert len(calls) == 3  # two retries after the first 429


def test_groq_client_honors_retry_after(monkeypatch):
    """Retry-After header wins over exponential backoff (sleep is stubbed)."""
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeResponse(429, text="slow down", headers={"Retry-After": "1"}))
    client = GroqClient(api_key="gsk-test", retry_backoff=0)  # backoff would sleep 0 — header forces 1s
    with pytest.raises(LLMError, match="429"):
        client.complete_json(task="jd_parser", system="s", prompt="p")
    assert sleeps == [1.0, 1.0]  # Retry-After honored, not the 0 backoff


def test_groq_client_bad_json_raises_llm_error(monkeypatch):
    fake = _FakeResponse(200, payload={"choices": [{"message": {"content": "not json"}}]})
    monkeypatch.setattr("requests.post", lambda *a, **k: fake)
    with pytest.raises(LLMError):
        GroqClient(api_key="gsk-test").complete_json(task="jd_parser", system="s", prompt="p")


def test_get_client_modes(monkeypatch):
    monkeypatch.delenv("RESUME_TAILOR_DRY_RUN", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert isinstance(get_client(), MockLLMClient)  # no key -> mock

    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert isinstance(get_client(), GroqClient)  # key -> Groq

    monkeypatch.setenv("RESUME_TAILOR_DRY_RUN", "1")
    assert isinstance(get_client("gsk-test"), MockLLMClient)  # dry-run wins
