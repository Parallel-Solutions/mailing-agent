from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from src.generator.delivery import manager_stats


def test_delivery_cache_build_is_single_flight(monkeypatch) -> None:
    job_id = "single-flight-job"
    build_started = Event()
    release_build = Event()
    calls: list[str] = []

    def fake_build(current_job_id: str, *, refresh: bool):
        calls.append(current_job_id)
        build_started.set()
        assert release_build.wait(timeout=2)
        return [{"job_id": current_job_id, "refresh": refresh}]

    manager_stats.invalidate_stats_cache(job_id)
    monkeypatch.setattr(manager_stats, "_build_delivery_rows_for_job", fake_build)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager_stats._load_delivery_for_jobs, (job_id,))
        assert build_started.wait(timeout=2)
        second = pool.submit(manager_stats._load_delivery_for_jobs, (job_id,))
        release_build.set()

        assert first.result(timeout=2) == [{"job_id": job_id, "refresh": False}]
        assert second.result(timeout=2) == [{"job_id": job_id, "refresh": False}]

    assert calls == [job_id]
    manager_stats.invalidate_stats_cache(job_id)


def test_explicit_delivery_refresh_rebuilds_warm_cache(monkeypatch) -> None:
    job_id = "explicit-refresh-job"
    calls: list[bool] = []

    def fake_build(current_job_id: str, *, refresh: bool):
        calls.append(refresh)
        return [{"job_id": current_job_id, "refresh": refresh}]

    manager_stats.invalidate_stats_cache(job_id)
    monkeypatch.setattr(manager_stats, "_build_delivery_rows_for_job", fake_build)

    assert manager_stats._load_delivery_for_jobs((job_id,)) == [
        {"job_id": job_id, "refresh": False},
    ]
    assert manager_stats._load_delivery_for_jobs((job_id,), refresh=True) == [
        {"job_id": job_id, "refresh": True},
    ]

    assert calls == [False, True]
