"""Nesine prematch bulteni buyudukce kirpilmamalidir."""

from __future__ import annotations

import io
import json
import unittest
from typing import Any
from unittest.mock import patch

from scrapers import soft_feed


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _payload_larger_than(min_bytes: int) -> bytes:
    template: dict[str, Any] = {
        "C": 0,
        "N": "Takim - Rakip",
        "ESD": 1787000000000,
        "MA": [],
    }
    per_event = len(json.dumps(template).encode("utf-8")) + 1
    count = min_bytes // per_event + 2
    events = [dict(template, C=index) for index in range(count)]
    return json.dumps({"sg": {"EA": events}}).encode("utf-8")


class PrematchFullReadTest(unittest.TestCase):
    def test_response_above_legacy_cap_is_parsed(self) -> None:
        raw = _payload_larger_than(9_000_000)

        with patch.object(soft_feed.urllib.request, "urlopen", return_value=_FakeResponse(raw)):
            payload = soft_feed._fetch_nesine_prematch_json()

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertIn("sg", payload)

    def test_response_above_hard_limit_is_rejected_not_truncated(self) -> None:
        raw = _payload_larger_than(2_000_000)

        with (
            patch.object(soft_feed, "_PREMATCH_HARD_LIMIT_BYTES", 1_000_000),
            patch.object(soft_feed.urllib.request, "urlopen", return_value=_FakeResponse(raw)),
        ):
            payload = soft_feed._fetch_nesine_prematch_json()

        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
