"""Tests for tw_scanner.data.base — DataNotAvailable, RateLimiter helpers."""


from datetime import UTC, date

from tw_scanner.data.base import DataNotAvailable, as_of_str, utc_now


class TestDataNotAvailable:
    def test_message_includes_dataset_and_reason(self) -> None:
        exc = DataNotAvailable("TaiwanStockPrice", "無法連線")
        assert "TaiwanStockPrice" in str(exc)
        assert "無法連線" in str(exc)

    def test_as_of_included_when_provided(self) -> None:
        d = date(2024, 1, 15)
        exc = DataNotAvailable("TaiwanStockInfo", "失敗", as_of=d)
        assert "2024-01-15" in str(exc)

    def test_as_of_none_does_not_appear(self) -> None:
        exc = DataNotAvailable("SomeDataset", "理由")
        assert "as_of" not in str(exc)

    def test_attributes_accessible(self) -> None:
        d = date(2023, 6, 30)
        exc = DataNotAvailable("DS", "R", as_of=d)
        assert exc.dataset == "DS"
        assert exc.reason == "R"
        assert exc.as_of == d


class TestHelpers:
    def test_as_of_str_format(self) -> None:
        assert as_of_str(date(2024, 3, 7)) == "2024-03-07"

    def test_utc_now_is_aware(self) -> None:
        dt = utc_now()
        assert dt.tzinfo is not None
        assert dt.tzinfo == UTC
