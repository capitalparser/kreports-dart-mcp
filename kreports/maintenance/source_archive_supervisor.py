"""Continuous, bounded-batch supervision for source archive campaigns."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import time
from typing import Callable

from kreports.collector.dart_api_key_ring import DartApiKeyRing
from kreports.collector.fetcher import dart_api_key_scope
from kreports.maintenance.source_archive_campaign import (
    ArchiveWriter,
    SourceArchivePlan,
    SourceArchiveReport,
    run_source_archive_shard,
)


LOCAL_BATCH_PAUSE_SECONDS = 30
PROVIDER_QUOTA_PROBE_SECONDS = 15 * 60
TRANSPORT_RETRY_SECONDS = 60
DRIVE_RETRY_SECONDS = 4 * 60
DRIVE_TRANSPORT_RETRY_SECONDS = 60
IDLE_SWEEP_SECONDS = 15 * 60
PARTIAL_RETRY_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class SourceArchiveSupervisorReport:
    batch_count: int
    sweep_count: int
    stop_reason: str | None
    last_report: SourceArchiveReport | None
    key_switch_count: int = 0


def supervise_source_archive(
    plan: SourceArchivePlan,
    archive: ArchiveWriter,
    *,
    max_dart_calls: int,
    run_shard: Callable[..., SourceArchiveReport] = run_source_archive_shard,
    sleeper: Callable[[float], object] = time.sleep,
    on_report: Callable[[SourceArchiveReport], object] | None = None,
    max_batches: int | None = None,
    local_batch_pause_seconds: int = LOCAL_BATCH_PAUSE_SECONDS,
    provider_quota_probe_seconds: int = PROVIDER_QUOTA_PROBE_SECONDS,
    partial_retry_after_seconds: int = PARTIAL_RETRY_SECONDS,
    key_ring: DartApiKeyRing | None = None,
) -> SourceArchiveSupervisorReport:
    """Keep progressing a frozen campaign without confusing two rate limits.

    A local physical-call boundary starts a fresh finite batch after a short
    pause.  A provider quota response is retried only after the longer probe
    interval.  Each shard invocation still owns its own request budget and
    immutable Drive writer lease.
    """
    if not isinstance(max_dart_calls, int) or max_dart_calls < 1:
        raise ValueError("max_dart_calls must be a positive integer")
    if max_batches is not None and (not isinstance(max_batches, int) or max_batches < 1):
        raise ValueError("max_batches must be a positive integer when provided")
    if plan.shard_count < 1:
        raise ValueError("plan must contain at least one shard")

    shard = 0
    year_index = 0
    batch_count = 0
    sweep_count = 0
    key_switch_count = 0
    last_report: SourceArchiveReport | None = None
    while True:
        key_scope = (
            dart_api_key_scope(key_ring.current_key)
            if key_ring is not None
            else nullcontext()
        )
        with key_scope:
            last_report = run_shard(
                plan,
                shard,
                archive,
                apply=True,
                max_dart_calls=max_dart_calls,
                partial_retry_after_seconds=partial_retry_after_seconds,
                target_year=plan.years[year_index],
            )
        batch_count += 1
        if on_report is not None:
            on_report(last_report)
        if last_report.stop_reason == "dart_auth_failure":
            if key_ring is not None and key_ring.advance_after_auth_failure():
                key_switch_count += 1
                continue
            return SourceArchiveSupervisorReport(
                batch_count, sweep_count, last_report.stop_reason, last_report,
                key_switch_count,
            )
        if max_batches is not None and batch_count >= max_batches:
            return SourceArchiveSupervisorReport(
                batch_count, sweep_count, "max_batches_reached", last_report,
                key_switch_count,
            )

        if last_report.stop_reason == "api_budget_exhausted":
            sleeper(local_batch_pause_seconds)
            continue
        if last_report.stop_reason == "dart_quota_failure":
            if key_ring is not None:
                if key_ring.advance_after_quota():
                    key_switch_count += 1
                    continue
                sleeper(provider_quota_probe_seconds)
                if key_ring.begin_new_quota_cycle():
                    key_switch_count += 1
                continue
            sleeper(provider_quota_probe_seconds)
            continue
        if last_report.stop_reason == "dart_transport_failure":
            sleeper(TRANSPORT_RETRY_SECONDS)
            continue
        if last_report.stop_reason == "drive_quota_exhausted":
            sleeper(DRIVE_RETRY_SECONDS)
            continue
        if last_report.stop_reason == "drive_transport_failure":
            sleeper(DRIVE_TRANSPORT_RETRY_SECONDS)
            continue
        if last_report.stop_reason is not None:
            return SourceArchiveSupervisorReport(
                batch_count, sweep_count, last_report.stop_reason, last_report,
                key_switch_count,
            )

        shard += 1
        if shard >= plan.shard_count:
            shard = 0
            year_index += 1
            if year_index >= len(plan.years):
                year_index = 0
                sweep_count += 1
                sleeper(IDLE_SWEEP_SECONDS)
