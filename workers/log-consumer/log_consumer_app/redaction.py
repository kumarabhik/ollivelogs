from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import cast

from log_consumer_app.settings import Settings

try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
except ImportError:  # pragma: no cover - optional dependency fallback
    AnalyzerEngine = None
    AnonymizerEngine = None
    OperatorConfig = None

log = logging.getLogger("log-consumer.redaction")

_MAX_PREVIEW_CHARS = 256

_CUSTOM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("INDIA_PHONE", re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
]


@dataclass(slots=True)
class RedactionResult:
    text: str
    entities_found: int


class TextRedactor:
    """Best-effort redaction with optional Presidio and guaranteed regex fallback."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.pii_redaction_enabled
        self._analyzer = None
        self._anonymizer = None
        if not self._enabled or AnalyzerEngine is None or AnonymizerEngine is None:
            return
        try:
            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
        except Exception as exc:  # pragma: no cover - depends on local NLP assets
            log.warning("presidio_initialization_failed", exc_info=exc)
            self._analyzer = None
            self._anonymizer = None

    def redact_preview(self, text: str | None) -> RedactionResult:
        if not text:
            return RedactionResult(text="", entities_found=0)

        working_text = text
        entity_count = 0
        if self._enabled:
            working_text, regex_count = _apply_regex_redaction(working_text)
            entity_count += regex_count
            if self._analyzer is not None and self._anonymizer is not None and OperatorConfig is not None:
                analyzer_results = self._analyzer.analyze(text=working_text, language="en")
                if analyzer_results:
                    operator_map = {
                        result.entity_type: cast(
                            object,
                            OperatorConfig("replace", {"new_value": f"<{result.entity_type}>"}),
                        )
                        for result in analyzer_results
                    }
                    anonymized = self._anonymizer.anonymize(
                        text=working_text,
                        analyzer_results=analyzer_results,
                        operators=operator_map,
                    )
                    working_text = anonymized.text
                    entity_count += len(analyzer_results)

        return RedactionResult(text=_truncate(working_text), entities_found=entity_count)


def _apply_regex_redaction(text: str) -> tuple[str, int]:
    redacted = text
    entity_count = 0
    for label, pattern in _CUSTOM_PATTERNS:
        redacted, replacements = pattern.subn(f"<{label}>", redacted)
        entity_count += replacements
    return redacted, entity_count


def _truncate(text: str) -> str:
    if len(text) <= _MAX_PREVIEW_CHARS:
        return text
    return text[: _MAX_PREVIEW_CHARS - 3] + "..."
