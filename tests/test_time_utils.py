import unittest
from datetime import datetime, timezone

from core.time_utils import parse_utc


class ParseUtcTests(unittest.TestCase):
    def test_z_suffix_is_utc(self) -> None:
        self.assertEqual(
            parse_utc("2026-06-28T02:00:00Z"),
            datetime(2026, 6, 28, 2, 0, tzinfo=timezone.utc),
        )

    def test_lowercase_z_suffix(self) -> None:
        self.assertEqual(
            parse_utc("2026-06-28t02:00:00z"),
            datetime(2026, 6, 28, 2, 0, tzinfo=timezone.utc),
        )

    def test_offset_is_normalized_to_utc(self) -> None:
        self.assertEqual(
            parse_utc("2026-06-28T05:00:00+03:00"),
            datetime(2026, 6, 28, 2, 0, tzinfo=timezone.utc),
        )

    def test_naive_value_assumed_utc(self) -> None:
        self.assertEqual(
            parse_utc("2026-06-28T02:00:00"),
            datetime(2026, 6, 28, 2, 0, tzinfo=timezone.utc),
        )

    def test_invalid_values_return_none(self) -> None:
        for value in ("", "   ", "not-a-date", None, 17):
            self.assertIsNone(parse_utc(value))


if __name__ == "__main__":
    unittest.main()
