from collections import deque

import pandas as pd
import requests

from src.data.twelvedata_loader import TwelveDataLoader
from src.utils.health import HealthMonitor
from src.utils.notifications import TelegramNotifier
from src.utils.state_store import StateStore


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": "ok"}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


class FakeSession:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []
        self.headers = {}
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def loader_config(**source_overrides):
    source = {
        "cache_seconds": 10,
        "max_stale_seconds": 30,
        "min_request_gap_seconds": 0,
        "requests_per_minute": 100,
        "daily_request_limit": 10,
        "retry": {
            "max_attempts": 3,
            "base_delay_seconds": 2,
            "max_delay_seconds": 30,
            "jitter_seconds": 0,
        },
    }
    source.update(source_overrides)
    return {"data_source": source}


def bars_payload():
    return {
        "status": "ok",
        "values": [
            {
                "datetime": "2026-08-18 12:00:00",
                "open": "100",
                "high": "105",
                "low": "99",
                "close": "103",
                "volume": "10",
            }
        ],
    }


def test_loader_retries_retry_after_and_counts_each_attempt():
    clock = FakeClock(1_756_000_000)
    sleeps = []
    session = FakeSession(
        [
            FakeResponse(429, {"status": "error"}, {"Retry-After": "4"}),
            FakeResponse(500, {"status": "error"}),
            FakeResponse(200, bars_payload()),
        ]
    )
    loader = TwelveDataLoader(
        loader_config(),
        "test-key",
        session=session,
        clock=clock,
        sleeper=lambda seconds: sleeps.append(seconds),
        random_source=lambda: 0,
    )

    frame = loader.fetch_data("XAUUSD", "H1", n_bars=1)

    assert isinstance(frame, pd.DataFrame)
    assert len(session.calls) == 3
    assert sleeps == [4.0, 4.0]
    assert loader.get_metrics()["retries"] == 2
    assert loader.get_metrics()["daily_requests"] == 3
    assert session.calls[0][2]["params"]["symbol"] == "XAU/USD"


def test_loader_enforces_daily_budget_and_resets_on_utc_day():
    clock = FakeClock(1_756_000_000)
    session = FakeSession([FakeResponse(200, bars_payload()), FakeResponse(200, bars_payload())])
    loader = TwelveDataLoader(
        loader_config(daily_request_limit=1),
        "test-key",
        session=session,
        clock=clock,
        sleeper=lambda seconds: None,
        random_source=lambda: 0,
    )

    assert loader.get_current_price("EURUSD")[0] is None
    assert len(session.calls) == 1
    assert loader.get_metrics()["daily_requests"] == 1

    loader._cache.clear()
    clock.advance(86400)
    assert loader.fetch_data("EURUSD", "H1", n_bars=1) is not None
    assert len(session.calls) == 2
    assert loader.get_metrics()["daily_requests"] == 1


def test_loader_serves_fresh_and_bounded_stale_cache():
    clock = FakeClock(1_756_000_000)
    session = FakeSession(
        [FakeResponse(200, bars_payload()), requests.exceptions.Timeout()]
    )
    loader = TwelveDataLoader(
        loader_config(retry={"max_attempts": 1}),
        "test-key",
        session=session,
        clock=clock,
        sleeper=lambda seconds: None,
        random_source=lambda: 0,
    )

    first = loader.fetch_data("EURUSD", "H1", n_bars=1)
    clock.advance(5)
    fresh = loader.fetch_data("EURUSD", "H1", n_bars=1)
    assert len(session.calls) == 1
    assert loader.get_metrics()["cache_hits"] == 1
    assert fresh.equals(first)

    clock.advance(10)
    stale = loader.fetch_data("EURUSD", "H1", n_bars=1)
    assert stale.equals(first)
    assert loader.get_metrics()["stale_cache_hits"] == 1

    clock.advance(31)
    assert loader.fetch_data("EURUSD", "H1", n_bars=1) is None


def test_telegram_persists_offset_and_ignores_unauthorized_chat(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "ok": True,
                    "result": [
                        {"update_id": 10, "message": {"chat": {"id": "999"}, "text": "/stop"}},
                        {"update_id": 11, "message": {"chat": {"id": "123"}, "text": "/status now"}},
                    ],
                },
            )
        ]
    )
    notifier = TelegramNotifier(
        "test-token",
        "123",
        config={"telegram": {"retry": {"max_attempts": 1}}},
        state_store=store,
        session=session,
    )

    assert notifier.get_updates() == ["/status"]
    assert store.get_telegram_offset("telegram:123") == 11
    assert session.calls[0][2]["params"]["offset"] == 0

    resumed = TelegramNotifier(
        "test-token",
        "123",
        config={"telegram": {"retry": {"max_attempts": 1}}},
        state_store=store,
        session=FakeSession([]),
    )
    assert resumed.last_update_id == 11


def test_telegram_retries_with_retry_after():
    sleeps = []
    session = FakeSession(
        [
            FakeResponse(429, {"ok": False}, {"Retry-After": "3"}),
            FakeResponse(200, {"ok": True, "result": []}),
        ]
    )
    notifier = TelegramNotifier(
        "test-token",
        "123",
        config={
            "telegram": {
                "retry": {
                    "max_attempts": 2,
                    "base_delay_seconds": 1,
                    "max_delay_seconds": 5,
                    "jitter_seconds": 0,
                }
            }
        },
        session=session,
        sleeper=lambda seconds: sleeps.append(seconds),
        random_source=lambda: 0,
    )

    assert notifier.get_updates() == []
    assert sleeps == [3.0]
    assert notifier.get_metrics()["retries"] == 1
    assert notifier.get_metrics()["status"] == "healthy"


def test_health_transitions_and_stale_evaluation(tmp_path):
    clock = FakeClock(1_756_000_000)
    store = StateStore(str(tmp_path / "state.db"))
    monitor = HealthMonitor(
        store,
        {"health": {"failure_threshold": 2, "loop_stale_seconds": 10, "data_stale_seconds": 100}},
        clock=clock,
    )

    assert monitor.record_success("market_data") is True
    assert monitor.drain_transitions()[0]["status"] == "healthy"
    assert monitor.record_failure("market_data", "timeout") is True
    assert monitor.get_component("market_data")["status"] == "degraded"
    assert monitor.record_failure("market_data", "timeout") is True
    assert monitor.get_component("market_data")["status"] == "unhealthy"
    assert monitor.record_success("market_data") is True
    assert monitor.get_component("market_data")["status"] == "healthy"

    monitor.drain_transitions()
    monitor.heartbeat()
    monitor.drain_transitions()
    clock.advance(11)
    transitions = monitor.evaluate()
    assert transitions
    assert transitions[-1]["component"] == "scan_loop"
    assert transitions[-1]["status"] == "degraded"
    assert store.get_health()
