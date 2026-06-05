"""Tests for Coinalyze API client."""
import pytest
from unittest.mock import patch, MagicMock


def test_symbol_converter():
    """Symbol converter maps BTCUSDT → BTCUSDT_PERP.A"""
    from coinalyze_client import _symbol
    assert _symbol("BTCUSDT") == "BTCUSDT_PERP.A"
    assert _symbol("ETHUSDT") == "ETHUSDT_PERP.A"
    assert _symbol("SOLUSDT") == "SOLUSDT_PERP.A"


def test_symbol_converter_no_double_usdt():
    """Symbol converter does not add USDT if already present."""
    from coinalyze_client import _symbol
    assert _symbol("BTCUSDT") == "BTCUSDT_PERP.A"
    # Already-formatted symbol should pass through
    assert _symbol("BTCUSDT_PERP.A") == "BTCUSDT_PERP.A"


def test_symbol_converter_base_only():
    """Symbol converter handles base-only symbols by appending USDT."""
    from coinalyze_client import _symbol
    result = _symbol("BTC")
    assert result == "BTCUSDT_PERP.A"


def test_get_all_structure():
    """get_all returns dict with 4 expected keys."""
    with patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")
    assert "oi" in result
    assert "liquidations" in result
    assert "funding" in result
    assert "long_short" in result


def test_get_open_interest_degrades():
    """get_open_interest returns {} on API failure."""
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_open_interest
        assert get_open_interest("BTCUSDT") == {}


def test_get_open_interest_aggregates_across_exchanges():
    """get_open_interest sums OI across all major perp venues that list the pair."""
    mock_response = [
        {"symbol": "BTCUSDT_PERP.A", "value": 100_000.0, "update": 0},
        {"symbol": "BTCUSDT.6",      "value":  50_000.0, "update": 0},
        {"symbol": "BTCUSDT_PERP.3", "value":  20_000.0, "update": 0},
    ]
    syms_mock = {"A": "BTCUSDT_PERP.A", "6": "BTCUSDT.6", "3": "BTCUSDT_PERP.3"}
    with patch("coinalyze_client._symbols_for_base", return_value=syms_mock), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_open_interest
        result = get_open_interest("BTCUSDT")
    assert result["oi_coins"] == 170_000.0
    assert result["oi_by_exchange"]["Binance"] == 100_000.0
    assert result["oi_by_exchange"]["Bybit"]    == 50_000.0
    assert result["oi_by_exchange"]["OKX"]      == 20_000.0
    assert result["exchange_count"] == 3
    assert result["oi_symbol"] == "BTCUSDT_PERP.A"


def test_get_open_interest_fallback_when_discovery_fails():
    """When _symbols_for_base returns {}, falls back to Binance-only query."""
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "value": 98765.432, "update": 0}]
    with patch("coinalyze_client._symbols_for_base", return_value={}), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_open_interest
        result = get_open_interest("BTCUSDT")
    assert result["oi_coins"] == 98765.432
    assert result["exchange_count"] == 1
    assert result["oi_by_exchange"] == {"Binance": 98765.432}


def test_get_funding_rate_parses_value_field():
    """get_funding_rate reads 'value' field (confirmed API shape)."""
    # Use 0.0002 (> 0.0001 threshold for longs_paying)
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "value": 0.0002, "update": 1778869122575}]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_funding_rate
        result = get_funding_rate("BTCUSDT")
    assert result["rate"] == 0.0002
    assert result["sentiment"] == "longs_paying"
    # annualized: 0.0002 * 3 * 365 * 100 = 21.9
    assert abs(result["annualized_pct"] - 21.9) < 0.01


def test_get_funding_rate_sentiment_labels():
    """Sentiment labels are applied correctly for various rate values."""
    from coinalyze_client import get_funding_rate

    cases = [
        (0.001,   "longs_paying_heavily"),
        (0.0003,  "longs_paying"),
        (0.00005, "neutral"),
        (-0.0003, "shorts_paying"),
    ]
    for rate, expected_sentiment in cases:
        mock = [{"value": rate}]
        with patch("coinalyze_client._get", return_value=mock):
            result = get_funding_rate("BTCUSDT")
        assert result["sentiment"] == expected_sentiment, f"rate={rate}"


def test_get_long_short_ratio_parses_history_endpoint():
    """get_long_short_ratio reads /long-short-ratio-history with nested history[] bars."""
    mock_response = [{
        "symbol": "BTCUSDT_PERP.A",
        "history": [{"t": 1780045200, "r": 1.5, "l": 60.0, "s": 40.0}],
    }]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_long_short_ratio
        result = get_long_short_ratio("BTCUSDT")
    assert result["ratio"] == 1.5
    assert result["longs_pct"] == 60.0
    assert result["shorts_pct"] == 40.0


def test_get_long_short_ratio_degrades_on_zero():
    """get_long_short_ratio returns {} when ratio is 0 (invalid)."""
    mock_response = [{
        "symbol": "BTCUSDT_PERP.A",
        "history": [{"t": 1780045200, "r": 0, "l": 0, "s": 0}],
    }]
    with patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_long_short_ratio
        assert get_long_short_ratio("BTCUSDT") == {}


def test_get_liquidations_aggregates_across_exchanges():
    """get_liquidations sums liquidation bars across all reporting venues."""
    mock_response = [
        {"symbol": "BTCUSDT_PERP.A",
         "history": [{"t": 1780045200, "l": 5.0, "s": 2.0}]},
        {"symbol": "BTCUSDT.6",
         "history": [{"t": 1780045200, "l": 3.0, "s": 1.0}]},
    ]
    syms_mock = {"A": "BTCUSDT_PERP.A", "6": "BTCUSDT.6"}
    with patch("coinalyze_client._symbols_for_base", return_value=syms_mock), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidations
        result = get_liquidations("BTCUSDT")
    assert result["liq_long_coins"] == 8.0
    assert result["liq_short_coins"] == 3.0
    assert result["liq_total_coins"] == 11.0
    assert result["exchange_count"] == 2


def test_get_liquidations_degrades():
    """get_liquidations returns {} on API failure."""
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_liquidations
        assert get_liquidations("BTCUSDT") == {}


def test_get_all_aggregates_all_sources():
    """get_all combines results from all sub-fetches."""
    oi_data     = [{"value": 50000.0, "symbol": "BTCUSDT_PERP.A", "update": 0},
                   {"value": 30000.0, "symbol": "BTCUSDT.6",      "update": 0}]
    funding_data = [{"value": 0.0002, "symbol": "BTCUSDT_PERP.A", "update": 0}]
    ls_data      = [{"symbol": "BTCUSDT_PERP.A",
                     "history": [{"t": 0, "r": 1.2, "l": 54.5, "s": 45.5}]}]
    liq_data     = [{"symbol": "BTCUSDT_PERP.A",
                     "history": [{"t": 0, "l": 5.0, "s": 3.0}]}]
    fbe_data     = [{"symbol": "BTCUSDT_PERP.A", "value": 0.0002, "update": 0},
                    {"symbol": "BTCUSDT.6",      "value": 0.0005, "update": 0}]

    def mock_get(path, params):
        if "open-interest-history" in path:
            return []
        if "open-interest" in path:
            return oi_data
        if "funding-rate" in path:
            return fbe_data if "," in params.get("symbols", "") else funding_data
        if "long-short-ratio" in path:
            return ls_data
        if "liquidation-history" in path:
            return liq_data
        return None

    syms_mock = {"A": "BTCUSDT_PERP.A", "6": "BTCUSDT.6"}
    with patch("coinalyze_client._symbols_for_base", return_value=syms_mock), \
         patch("coinalyze_client._get", side_effect=mock_get):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")

    assert result["oi"]["oi_coins"] == 80000.0
    assert result["oi"]["exchange_count"] == 2
    assert result["funding"]["sentiment"] == "longs_paying"
    assert result["long_short"]["longs_pct"] == pytest.approx(54.5, abs=0.5)
    assert result["liquidations"]["liq_total_coins"] == 8.0
    assert result["funding_by_exchange"]["binance"] == 0.0002
    assert result["funding_by_exchange"]["bybit"]   == 0.0005


def test_get_all_partial_failure():
    """get_all returns {} for failed sub-fetches but succeeds for others."""
    def mock_get(path, params):
        if "open-interest-history" in path:
            return None
        if "open-interest" in path:
            return [{"value": 50000.0, "symbol": "BTCUSDT_PERP.A", "update": 0}]
        return None

    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", side_effect=mock_get):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")

    assert result["oi"]["oi_coins"] == 50000.0
    assert result["funding"] == {}
    assert result["long_short"] == {}
    assert result["liquidations"] == {}


def test_api_key_loaded():
    """COINALYZE_API_KEY must be set in environment when running on Pi."""
    import os
    key = os.environ.get("COINALYZE_API_KEY", "")
    if not key:
        pytest.skip("COINALYZE_API_KEY not in local environment")
    assert len(key) > 10


def test_fetch_coinalyze_adapter():
    """fetch_coinalyze adapter in data_sources returns dict."""
    mock_result = {
        "oi": {"oi_coins": 50000.0, "oi_symbol": "BTCUSDT_PERP.A"},
        "liquidations": {},
        "funding": {},
        "long_short": {},
    }
    with patch("coinalyze_client.get_all", return_value=mock_result):
        with patch("coinalyze_client._API_KEY", "test-key-12345"):
            from data_sources import fetch_coinalyze
            result = fetch_coinalyze("BTCUSDT")
    assert isinstance(result, dict)
    assert "oi" in result


def test_fetch_coinalyze_adapter_no_key():
    """fetch_coinalyze returns {} when API key is not set."""
    with patch("coinalyze_client._API_KEY", ""):
        from data_sources import fetch_coinalyze
        result = fetch_coinalyze("BTCUSDT")
    assert result == {}


def test_get_all_has_new_keys():
    """get_all() must return funding_by_exchange and liquidation_trend keys."""
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_all
        result = get_all("BTCUSDT")
    assert "funding_by_exchange" in result
    assert "liquidation_trend" in result


def test_get_funding_by_exchange_degrades():
    """get_funding_by_exchange returns {} on API failure."""
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_funding_by_exchange
        assert get_funding_by_exchange("BTCUSDT") == {}


def test_get_funding_by_exchange_parses_per_exchange():
    """get_funding_by_exchange reads per-exchange rates and computes spread."""
    mock_response = [
        {"symbol": "BTCUSDT_PERP.A", "value": 0.0001, "update": 0},
        {"symbol": "BTCUSDT.6",      "value": 0.00007, "update": 0},
        {"symbol": "BTCUSDT_PERP.3", "value": 0.00013, "update": 0},
    ]
    syms_mock = {"A": "BTCUSDT_PERP.A", "6": "BTCUSDT.6", "3": "BTCUSDT_PERP.3"}
    with patch("coinalyze_client._symbols_for_base", return_value=syms_mock), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_funding_by_exchange
        result = get_funding_by_exchange("BTCUSDT")
    assert result["binance"] == 0.0001
    assert result["bybit"] == 0.00007
    assert result["okx"] == 0.00013
    # spread = (0.00013 - 0.00007) * 100 = 0.006
    assert abs(result["spread_pct"] - 0.006) < 0.0001


def test_get_liquidation_trend_degrades():
    """get_liquidation_trend returns {} on API failure."""
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=None):
        from coinalyze_client import get_liquidation_trend
        assert get_liquidation_trend("BTCUSDT") == {}


def test_get_liquidation_trend_too_few_records():
    """get_liquidation_trend returns {} when fewer than 6 hourly bars in history."""
    mock_response = [{"symbol": "BTCUSDT_PERP.A",
                      "history": [{"t": 1, "l": 1.0, "s": 0.5}]}]
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidation_trend
        assert get_liquidation_trend("BTCUSDT") == {}


def test_get_liquidation_trend_accelerating():
    """get_liquidation_trend detects accelerating when recent 6h >> older 18h.

    Bars need non-zero timestamps so the aggregator keys them correctly.
    """
    old_bars = [{"t": 1000 + i, "l": 0.1, "s": 0.1} for i in range(18)]
    recent_bars = [{"t": 2000 + i, "l": 10.0, "s": 10.0} for i in range(6)]
    mock_response = [{"symbol": "BTCUSDT_PERP.A",
                      "history": old_bars + recent_bars}]
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidation_trend
        result = get_liquidation_trend("BTCUSDT")
    assert result["trend"] == "accelerating"
    assert result["total_24h_coins"] > 0
    assert result["recent_6h_coins"] > 0
    assert result["exchange_count"] == 1


def test_get_liquidation_trend_dominant_shorts():
    """get_liquidation_trend reports dominant_side=shorts when short liqs dominate."""
    bars = [{"t": 1000 + i, "l": 0.1, "s": 5.0} for i in range(24)]
    mock_response = [{"symbol": "BTCUSDT_PERP.A", "history": bars}]
    with patch("coinalyze_client._symbols_for_base", return_value={"A": "BTCUSDT_PERP.A"}), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidation_trend
        result = get_liquidation_trend("BTCUSDT")
    assert result["dominant_side"] == "shorts"


def test_get_liquidation_trend_aggregates_across_exchanges():
    """get_liquidation_trend sums per-hour across exchanges so a single venue
    with too-few bars still contributes once another venue covers the gap."""
    bars_a = [{"t": 1000 + i, "l": 1.0, "s": 1.0} for i in range(24)]
    bars_b = [{"t": 1000 + i, "l": 2.0, "s": 2.0} for i in range(24)]
    mock_response = [
        {"symbol": "BTCUSDT_PERP.A", "history": bars_a},
        {"symbol": "BTCUSDT.6",      "history": bars_b},
    ]
    syms_mock = {"A": "BTCUSDT_PERP.A", "6": "BTCUSDT.6"}
    with patch("coinalyze_client._symbols_for_base", return_value=syms_mock), \
         patch("coinalyze_client._get", return_value=mock_response):
        from coinalyze_client import get_liquidation_trend
        result = get_liquidation_trend("BTCUSDT")
    # 24 hours × (1+2) longs + (1+2) shorts = 144 coins total
    assert result["total_24h_coins"] == pytest.approx(144.0)
    assert result["exchange_count"] == 2


def test_symbols_for_base_uses_market_index():
    """_symbols_for_base picks USDT perps from the discovered market index."""
    fake_markets = [
        {"symbol": "BTCUSDT_PERP.A", "exchange": "A", "base_asset": "BTC",
         "quote_asset": "USDT", "is_perpetual": True},
        {"symbol": "BTCUSDT.6", "exchange": "6", "base_asset": "BTC",
         "quote_asset": "USDT", "is_perpetual": True},
        {"symbol": "BTCBUSD.A", "exchange": "A", "base_asset": "BTC",
         "quote_asset": "BUSD", "is_perpetual": True},  # wrong quote
        {"symbol": "ETHUSDT.6", "exchange": "6", "base_asset": "ETH",
         "quote_asset": "USDT", "is_perpetual": True},
        {"symbol": "BTCUSDT_2026.A", "exchange": "A", "base_asset": "BTC",
         "quote_asset": "USDT", "is_perpetual": False},  # dated, not perp
    ]
    import coinalyze_client as cc
    cc._markets_index.clear()
    cc._markets_index_ts = 0
    with patch("coinalyze_client._get", return_value=fake_markets):
        syms = cc._symbols_for_base("BTC")
    assert syms == {"A": "BTCUSDT_PERP.A", "6": "BTCUSDT.6"}
