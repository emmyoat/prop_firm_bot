from datetime import datetime, timezone
import pytest
from main import is_market_closed, is_friday_close, in_active_session, get_active_session


@pytest.fixture
def base_config():
    return {
        "system": {
            "active_sessions": [
                {"name": "London", "start_utc": 8, "end_utc": 12},
                {"name": "London/NY Overlap", "start_utc": 12, "end_utc": 17},
                {"name": "New York", "start_utc": 18, "end_utc": 22},
                {"name": "Late NY / Asia", "start_utc": 22, "end_utc": 24},
            ]
        },
        "risk": {
            "friday_exit_hour": 21,
            "sunday_open_hour": 21,
        },
    }


def test_market_open_midweek(base_config):
    # Monday 10:00 UTC
    mon = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    assert not is_market_closed(base_config, now=mon)
    assert in_active_session(base_config, now=mon)
    assert get_active_session(base_config, now=mon) == "London"

    # Wednesday 15:00 UTC
    wed = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
    assert not is_market_closed(base_config, now=wed)
    assert in_active_session(base_config, now=wed)
    assert get_active_session(base_config, now=wed) == "London/NY Overlap"


def test_friday_close_transition(base_config):
    # Friday 20:59 UTC (still open)
    fri_open = datetime(2026, 9, 18, 20, 59, tzinfo=timezone.utc)
    assert not is_market_closed(base_config, now=fri_open)
    assert in_active_session(base_config, now=fri_open)
    assert get_active_session(base_config, now=fri_open) == "New York"

    # Friday 21:00 UTC (market closed for weekend)
    fri_close = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)
    assert is_market_closed(base_config, now=fri_close)
    assert is_friday_close(base_config, now=fri_close)
    assert not in_active_session(base_config, now=fri_close)
    assert get_active_session(base_config, now=fri_close) == "Market-Closed"


def test_saturday_closed_all_day(base_config):
    sat_morning = datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc)
    sat_afternoon = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
    sat_night = datetime(2026, 9, 19, 23, 0, tzinfo=timezone.utc)

    for dt in [sat_morning, sat_afternoon, sat_night]:
        assert is_market_closed(base_config, now=dt)
        assert not in_active_session(base_config, now=dt)
        assert get_active_session(base_config, now=dt) == "Market-Closed"


def test_sunday_closed_until_evening_open(base_config):
    # Sunday 10:55 UTC (today's scenario where API was being queried)
    sun_morning = datetime(2026, 9, 20, 10, 55, tzinfo=timezone.utc)
    assert is_market_closed(base_config, now=sun_morning)
    assert not in_active_session(base_config, now=sun_morning)
    assert get_active_session(base_config, now=sun_morning) == "Market-Closed"

    # Sunday 20:59 UTC (1 minute before open)
    sun_pre_open = datetime(2026, 9, 20, 20, 59, tzinfo=timezone.utc)
    assert is_market_closed(base_config, now=sun_pre_open)
    assert not in_active_session(base_config, now=sun_pre_open)

    # Sunday 21:00 UTC (market opens)
    sun_open = datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc)
    assert not is_market_closed(base_config, now=sun_open)
    assert in_active_session(base_config, now=sun_open)
    assert get_active_session(base_config, now=sun_open) == "New York"

    # Sunday 22:30 UTC (Late NY / Asia session)
    sun_asia = datetime(2026, 9, 20, 22, 30, tzinfo=timezone.utc)
    assert not is_market_closed(base_config, now=sun_asia)
    assert in_active_session(base_config, now=sun_asia)
    assert get_active_session(base_config, now=sun_asia) == "Late NY / Asia"


def test_custom_open_close_hours():
    cfg = {
        "system": {"active_sessions": []},
        "risk": {
            "friday_exit_hour": 22,
            "sunday_open_hour": 22,
        },
    }
    # Friday 21:30 is open under this custom config
    fri = datetime(2026, 9, 18, 21, 30, tzinfo=timezone.utc)
    assert not is_market_closed(cfg, now=fri)

    # Friday 22:00 is closed
    fri_closed = datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc)
    assert is_market_closed(cfg, now=fri_closed)

    # Sunday 21:30 is still closed
    sun_closed = datetime(2026, 9, 20, 21, 30, tzinfo=timezone.utc)
    assert is_market_closed(cfg, now=sun_closed)

    # Sunday 22:00 is open
    sun_open = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    assert not is_market_closed(cfg, now=sun_open)
