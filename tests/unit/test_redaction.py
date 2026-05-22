from __future__ import annotations

from dataclasses import dataclass

import pytest
from faker import Faker
from log_consumer_app.redaction import TextRedactor
from log_consumer_app.settings import Settings

pytestmark = pytest.mark.unit


def test_presidio_analyzer_and_anonymizer_are_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAnalyzer:
        def analyze(self, text: str, language: str) -> list[object]:
            assert language == "en"
            assert "alice@example.com" in text
            return [type("Result", (), {"entity_type": "EMAIL_ADDRESS"})()]

    @dataclass(slots=True)
    class FakeAnonymizeResult:
        text: str

    class FakeAnonymizer:
        def anonymize(self, text: str, analyzer_results: list[object], operators: dict[str, object]) -> object:
            assert analyzer_results
            assert "EMAIL_ADDRESS" in operators
            return FakeAnonymizeResult(text=text.replace("alice@example.com", "<EMAIL_ADDRESS>"))

    class FakeOperatorConfig:
        def __init__(self, operator_name: str, params: dict[str, str]) -> None:
            self.operator_name = operator_name
            self.params = params

    monkeypatch.setattr("log_consumer_app.redaction.AnalyzerEngine", FakeAnalyzer)
    monkeypatch.setattr("log_consumer_app.redaction.AnonymizerEngine", FakeAnonymizer)
    monkeypatch.setattr("log_consumer_app.redaction.OperatorConfig", FakeOperatorConfig)

    redactor = TextRedactor(Settings(pii_redaction_enabled=True))
    result = redactor.redact_preview("reach me at alice@example.com")

    assert result.text == "reach me at <EMAIL_ADDRESS>"
    assert result.entities_found == 1


def test_custom_regex_redaction_masks_indian_identifiers() -> None:
    redactor = TextRedactor(Settings(pii_redaction_enabled=True))
    result = redactor.redact_preview(
        "Aadhaar 1234 1234 1234, PAN ABCDE1234F, phone +91 9876543210, IFSC HDFC0001234"
    )

    assert "<AADHAAR>" in result.text
    assert "<PAN>" in result.text
    assert "<INDIA_PHONE>" in result.text
    assert "<IFSC>" in result.text
    assert result.entities_found >= 4


def test_synthetic_faker_corpus_masks_indian_identifiers() -> None:
    redactor = TextRedactor(Settings(pii_redaction_enabled=True))
    fake = Faker()
    fake.seed_instance(22)

    for _ in range(12):
        aadhaar = (
            f"{fake.numerify(text='####')} "
            f"{fake.numerify(text='####')} "
            f"{fake.numerify(text='####')}"
        )
        pan = (
            fake.lexify(text="?????").upper()
            + fake.numerify(text="####")
            + fake.lexify(text="?").upper()
        )
        phone = f"+91 {fake.numerify(text='9#########')}"
        ifsc = fake.lexify(text="????").upper() + "0" + fake.bothify(text="######").upper()
        sample = (
            f"{fake.name()} from {fake.city()} can be reached at {fake.email()}. "
            f"Aadhaar {aadhaar}; PAN {pan}; phone {phone}; IFSC {ifsc}."
        )

        result = redactor.redact_preview(sample)

        assert aadhaar not in result.text
        assert pan not in result.text
        assert phone not in result.text
        assert ifsc not in result.text
        assert "<AADHAAR>" in result.text
        assert "<PAN>" in result.text
        assert "<INDIA_PHONE>" in result.text
        assert "<IFSC>" in result.text
