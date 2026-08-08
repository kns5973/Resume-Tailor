import pytest

from resume_tailor.jd_parser import parse_jd
from resume_tailor.llm import LLMError, MockLLMClient


def test_parse_jd_with_default_mock():
    jd = parse_jd("Senior Backend Engineer...", client=MockLLMClient())
    assert jd.title == "Senior Backend Engineer"
    assert len(jd.requirements) == 3
    assert jd.requirements[0].priority == "must_have"
    assert "redis" in jd.requirements[0].keywords


def test_parse_jd_retries_on_bad_json():
    client = MockLLMClient(
        {
            "jd_parser": [
                '{"title": "broken", "requirements": not-json',
                '{"title": "Fixed", "company": "", "requirements": []}',
            ]
        }
    )
    jd = parse_jd("JD", client=client)
    assert jd.title == "Fixed"
    assert len(client.calls) == 2  # retried exactly once


def test_parse_jd_exhausts_retries():
    client = MockLLMClient({"jd_parser": ["definitely not json"]})  # only 1 queued -> reused
    with pytest.raises(LLMError):
        parse_jd("JD", client=client)
    assert len(client.calls) == 2  # initial + 1 retry


def test_parse_jd_rejects_invalid_schema():
    client = MockLLMClient(
        {"jd_parser": ['{"title": 123, "requirements": [{"requirement": "x"}]}']}
    )
    with pytest.raises(LLMError):
        parse_jd("JD", client=client)
