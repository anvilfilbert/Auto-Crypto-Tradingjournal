"""Tests for Feature 13 — Supply/Demand zones with order-absorption decay."""
import sys
import os
import sqlite3
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load():
    try:
        from chart_supply_demand import (detect_sd_zones, zone_strength,
                                          upsert_zone, record_touches_at_price,
                                          sd_zone_weight,
                                          ZONE_INVALIDATE_TOUCHES)
        return (detect_sd_zones, zone_strength, upsert_zone,
                record_touches_at_price, sd_zone_weight,
                ZONE_INVALIDATE_TOUCHES)
    except ImportError as e:
        pytest.skip(f"chart_supply_demand import failed: {e}")


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE sd_zones (
            id INTEGER PRIMARY KEY,
            symbol TEXT, timeframe TEXT, zone_type TEXT,
            top REAL, bottom REAL, touches INTEGER, valid INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT
        )
    """)
    return conn


class TestZoneStrength:
    def test_touches_zero_one_full_strength(self):
        _, fn, *_ = _load()
        assert fn(0) == 1.0
        assert fn(1) == 1.0

    def test_touches_two_half_strength(self):
        _, fn, *_ = _load()
        assert fn(2) == 0.5

    def test_touches_three_invalid(self):
        _, fn, *_ = _load()
        assert fn(3) == 0.0
        assert fn(10) == 0.0


class TestPersistence:
    def test_upsert_new_zone(self, db):
        _, _, upsert, *_ = _load()
        zone = {"zone_type": "demand", "top": 100.5, "bottom": 99.5}
        zone_id = upsert(db, "BTCUSDT", "4H", zone)
        assert zone_id > 0
        row = db.execute("SELECT * FROM sd_zones WHERE id=?", (zone_id,)).fetchone()
        assert row["zone_type"] == "demand"
        assert row["top"] == 100.5
        assert row["touches"] == 0
        assert row["valid"] == 1

    def test_upsert_existing_overlap_doesnt_duplicate(self, db):
        _, _, upsert, *_ = _load()
        z1 = {"zone_type": "demand", "top": 100.5, "bottom": 99.5}
        z2 = {"zone_type": "demand", "top": 100.6, "bottom": 100.0}  # overlaps z1
        id1 = upsert(db, "BTCUSDT", "4H", z1)
        id2 = upsert(db, "BTCUSDT", "4H", z2)
        # Should return same id (overlap detected)
        assert id1 == id2
        # Only one row
        count = db.execute("SELECT COUNT(*) FROM sd_zones").fetchone()[0]
        assert count == 1

    def test_record_touches_increments(self, db):
        _, _, upsert, record_touches, _, _ = _load()
        zone = {"zone_type": "demand", "top": 100.5, "bottom": 99.5}
        upsert(db, "BTCUSDT", "4H", zone)
        # Current price within zone
        record_touches(db, "BTCUSDT", "4H", current_price=100.0)
        row = db.execute("SELECT touches, valid FROM sd_zones").fetchone()
        assert row["touches"] == 1
        assert row["valid"] == 1

    def test_record_touches_invalidates_at_three(self, db):
        _, _, upsert, record_touches, _, INVALID = _load()
        zone = {"zone_type": "demand", "top": 100.5, "bottom": 99.5}
        upsert(db, "BTCUSDT", "4H", zone)
        for _ in range(INVALID):
            record_touches(db, "BTCUSDT", "4H", current_price=100.0)
        row = db.execute("SELECT touches, valid FROM sd_zones").fetchone()
        assert row["touches"] == INVALID
        assert row["valid"] == 0


class TestZoneWeight:
    def test_long_demand_below_supports(self, db):
        _, _, upsert, _, weight_fn, _ = _load()
        zone = {"zone_type": "demand", "top": 99.0, "bottom": 98.0}
        upsert(db, "BTCUSDT", "4H", zone)
        # Long, current price above demand zone → +0.3 (full strength, 0 touches)
        w, label = weight_fn(db, "BTCUSDT", "4H", current_price=100.0,
                              direction="Long")
        assert w == 0.3
        assert "demand" in label.lower()

    def test_no_zone_no_weight(self, db):
        _, _, _, _, weight_fn, _ = _load()
        w, _ = weight_fn(db, "BTCUSDT", "4H", current_price=100.0, direction="Long")
        assert w == 0.0

    def test_invalid_zone_no_weight(self, db):
        _, _, upsert, record_touches, weight_fn, INVALID = _load()
        zone = {"zone_type": "demand", "top": 99.0, "bottom": 98.0}
        upsert(db, "BTCUSDT", "4H", zone)
        # Knock out the zone with 3 touches
        for _ in range(INVALID):
            record_touches(db, "BTCUSDT", "4H", current_price=98.5)
        # Now zone is invalid — no contribution
        w, _ = weight_fn(db, "BTCUSDT", "4H", current_price=100.0, direction="Long")
        assert w == 0.0

    def test_half_strength_after_two_touches(self, db):
        _, _, upsert, record_touches, weight_fn, _ = _load()
        zone = {"zone_type": "demand", "top": 99.0, "bottom": 98.0}
        upsert(db, "BTCUSDT", "4H", zone)
        record_touches(db, "BTCUSDT", "4H", current_price=98.5)
        record_touches(db, "BTCUSDT", "4H", current_price=98.5)
        # 2 touches = half strength → 0.3 * 0.5 = 0.15
        w, _ = weight_fn(db, "BTCUSDT", "4H", current_price=100.0, direction="Long")
        assert w == 0.15


class TestDetectSDZones:
    def test_short_df_returns_empty(self):
        detect, *_ = _load()
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame([{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 10)
        assert detect(df, atr_value=1.0) == []

    def test_none_df_returns_empty(self):
        detect, *_ = _load()
        assert detect(None) == []
