"""Tests for dedup, cooldown, rate limits, and backpressure (M27-04/M27-05)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hiveplane.fleet.triggers import TriggerEvent, TriggerOutcome, TriggerSource
from hiveplane.triggers.limiter import LimiterDecision, TokenBucket, TriggerLimiter
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import InMemoryTriggerStore

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "t1",
        "source": "github",
        "target": {"kind": "workload", "ref": "agent-1"},
        "filter": {"event": ["push"]},
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _accepted_event(event_id: str, *, at: datetime, dedup_key: str | None = None) -> TriggerEvent:
    return TriggerEvent(
        event_id=event_id,
        trigger_id="t1",
        source=TriggerSource.GITHUB,
        payload={},
        received_at=at,
        dedup_key=dedup_key,
        outcome=TriggerOutcome.ACCEPTED,
    )


def _limiter(
    store: InMemoryTriggerStore | None = None,
    *,
    global_max_per_minute: int = 600,
    global_burst: int = 0,
) -> TriggerLimiter:
    return TriggerLimiter(
        store or InMemoryTriggerStore(),
        clock=lambda: _NOW,
        global_max_per_minute=global_max_per_minute,
        global_burst=global_burst,
    )


def test_token_bucket_allows_up_to_capacity_then_denies() -> None:
    bucket = TokenBucket(rate_per_minute=1, burst=0, clock=lambda: _NOW)
    assert bucket.allow(_NOW) is True
    assert bucket.allow(_NOW) is False


def test_token_bucket_refills_over_time() -> None:
    now = {"value": _NOW}
    bucket = TokenBucket(rate_per_minute=60, burst=0, clock=lambda: now["value"])
    assert bucket.allow(now["value"]) is True
    now["value"] = _NOW + timedelta(seconds=60)
    assert bucket.allow(now["value"]) is True


def test_duplicate_dedup_key_is_suppressed() -> None:
    store = InMemoryTriggerStore()
    store.add_event(
        _accepted_event("ev-1", at=_NOW - timedelta(minutes=5), dedup_key="acme/1")
    )
    limiter = _limiter(store)
    decision = limiter.check(_spec(dedup={"key": "x", "window_minutes": 30}), dedup_key="acme/1")
    assert decision is LimiterDecision.DEDUPLICATED


def test_dedup_key_outside_window_is_accepted() -> None:
    store = InMemoryTriggerStore()
    store.add_event(
        _accepted_event("ev-1", at=_NOW - timedelta(minutes=45), dedup_key="acme/1")
    )
    limiter = _limiter(store)
    decision = limiter.check(_spec(dedup={"key": "x", "window_minutes": 30}), dedup_key="acme/1")
    assert decision is LimiterDecision.ACCEPT


def test_cooldown_suppresses_second_event() -> None:
    store = InMemoryTriggerStore()
    store.add_event(_accepted_event("ev-1", at=_NOW - timedelta(seconds=10)))
    limiter = _limiter(store)
    assert limiter.check(_spec(cooldown_seconds=60)) is LimiterDecision.SUPPRESSED_COOLDOWN


def test_cooldown_expires() -> None:
    store = InMemoryTriggerStore()
    store.add_event(_accepted_event("ev-1", at=_NOW - timedelta(seconds=120)))
    limiter = _limiter(store)
    assert limiter.check(_spec(cooldown_seconds=60)) is LimiterDecision.ACCEPT


def test_per_trigger_rate_limit_rejects_excess() -> None:
    limiter = _limiter()
    spec = _spec(rate_limit={"max_per_minute": 1, "burst": 0})
    assert limiter.check(spec) is LimiterDecision.ACCEPT
    assert limiter.check(spec) is LimiterDecision.REJECTED_RATE


def test_global_backpressure_rejects_when_saturated() -> None:
    limiter = _limiter(global_max_per_minute=1, global_burst=0)
    assert limiter.check(_spec(id="a")) is LimiterDecision.ACCEPT
    assert limiter.check(_spec(id="b")) is LimiterDecision.REJECTED_BACKPRESSURE


def test_no_constraints_accepts() -> None:
    assert _limiter().check(_spec()) is LimiterDecision.ACCEPT


def test_concurrent_duplicate_dedup_key_starts_one_run() -> None:
    limiter = _limiter()
    spec = _spec(dedup={"key": "{{ event.number }}", "window_minutes": 30})
    first = limiter.check(spec, dedup_key="acme/1")
    second = limiter.check(spec, dedup_key="acme/1")
    assert first is LimiterDecision.ACCEPT
    assert second is LimiterDecision.DEDUPLICATED


def test_released_dedup_key_can_be_reclaimed() -> None:
    limiter = _limiter()
    spec = _spec(dedup={"key": "{{ event.number }}", "window_minutes": 30})
    assert limiter.check(spec, dedup_key="acme/1") is LimiterDecision.ACCEPT
    limiter.release_dedup(spec, "acme/1")
    assert limiter.check(spec, dedup_key="acme/1") is LimiterDecision.ACCEPT


class _CountingStore(InMemoryTriggerStore):
    """Records which store methods the hot path calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def list_events(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        self.calls.append("list_events")
        return super().list_events(*args, **kwargs)  # type: ignore[arg-type]

    def last_accepted_event(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        self.calls.append("last_accepted_event")
        return super().last_accepted_event(*args, **kwargs)  # type: ignore[arg-type]

    def find_dedup_event(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        self.calls.append("find_dedup_event")
        return super().find_dedup_event(*args, **kwargs)  # type: ignore[arg-type]

    def claim_dedup(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        self.calls.append("claim_dedup")
        return super().claim_dedup(*args, **kwargs)  # type: ignore[arg-type]


def test_cooldown_and_dedup_queries_are_bounded() -> None:
    store = _CountingStore()
    limiter = _limiter(store)
    spec = _spec(cooldown_seconds=60, dedup={"key": "x", "window_minutes": 30})

    assert limiter.check(spec, dedup_key="acme/1") is LimiterDecision.ACCEPT

    # The hot path uses bounded lookups, never a full event-history scan.
    assert "list_events" not in store.calls
    assert "last_accepted_event" in store.calls
    assert "find_dedup_event" in store.calls
    assert "claim_dedup" in store.calls
