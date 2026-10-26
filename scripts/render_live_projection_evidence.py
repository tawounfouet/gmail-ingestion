#!/usr/bin/env python
"""Render redacted 0.5.0 stable projection evidence from successful live qualification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_EVENT_TYPES = (
    "MESSAGE_ADDED",
    "MESSAGE_DELETED",
    "LABEL_ADDED",
    "LABEL_REMOVED",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Gmail Ingestion 0.5.0 live projection qualification JSON and "
            "render redacted stable release evidence."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("qualification/v0.5.0-live-evidence.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/releases/v0.5.0-evidence.md"),
    )
    parser.add_argument("--candidate-commit", required=True)
    return parser.parse_args()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read live qualification evidence {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Live projection evidence must contain a JSON object")
    return payload


def _validated(
    payload: dict[str, Any],
) -> tuple[str, str, dict[str, int], dict[str, bool], dict[str, Any]]:
    if payload.get("schema_version") != 1:
        raise SystemExit("Unsupported live projection evidence schema")
    if payload.get("live_gate_passed") is not True:
        raise SystemExit("Live projection qualification did not pass")
    if payload.get("stable_eligible") is not True:
        raise SystemExit("Live projection qualification is not stable-eligible")

    required = payload.get("required_event_types")
    if not isinstance(required, list) or set(required) != set(_EVENT_TYPES):
        raise SystemExit("Stable projection evidence requires all four Gmail History event types")

    observed_raw = payload.get("observed_event_types")
    if not isinstance(observed_raw, dict):
        raise SystemExit("Live projection evidence is missing observed event counts")
    observed: dict[str, int] = {}
    for event_type in _EVENT_TYPES:
        value = observed_raw.get(event_type)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SystemExit(f"Stable evidence requires observed {event_type} >= 1")
        observed[event_type] = value

    checks_raw = payload.get("checks")
    if not isinstance(checks_raw, dict) or not checks_raw:
        raise SystemExit("Live projection evidence is missing checks")
    checks: dict[str, bool] = {}
    for key, value in checks_raw.items():
        if not isinstance(key, str) or not isinstance(value, bool):
            raise SystemExit("Live projection checks must be boolean")
        checks[key] = value
    failed = sorted(key for key, value in checks.items() if not value)
    if failed:
        raise SystemExit(f"Live projection evidence contains failed checks: {failed}")

    projection = payload.get("projection")
    if not isinstance(projection, dict):
        raise SystemExit("Live projection evidence is missing projection convergence evidence")
    if projection.get("logical_digest") != projection.get("rebuild_digest"):
        raise SystemExit("Projection logical digest does not equal rebuild digest")
    if projection.get("promoted") is not True:
        raise SystemExit("Qualified projection was not promoted")
    if projection.get("replay_already_current") is not True:
        raise SystemExit("Qualified projection replay was not idempotent")

    version = payload.get("qualified_version")
    qualified_at = payload.get("qualified_at")
    if version not in {"0.5.0rc1", "0.5.0"}:
        raise SystemExit(f"Unexpected qualified version: {version!r}")
    if not isinstance(qualified_at, str) or not qualified_at:
        raise SystemExit("Live projection qualification timestamp is missing")
    return version, qualified_at, observed, checks, projection


def _markdown(
    *,
    version: str,
    qualified_at: str,
    candidate_commit: str,
    observed: dict[str, int],
    checks: dict[str, bool],
    projection: dict[str, Any],
) -> str:
    event_lines = "\n".join(
        f"{event_type:<18} {observed[event_type]}"
        for event_type in _EVENT_TYPES
    )
    check_lines = "\n".join(
        f"{key:<48} PASS"
        for key in sorted(checks)
    )
    incremental_batches = projection.get("incremental_batch_count")
    rebuild_batches = projection.get("rebuild_batch_count")
    return f"""# Gmail Ingestion 0.5.0 — Live Projection Qualification Evidence

## Status

**PASS**

The real Gmail OAuth/API-client path and deterministic Current State projection were exercised from:

```text
candidate commit: {candidate_commit}
qualified version: {version}
qualified at:      {qualified_at}
```

This evidence is intentionally redacted. It excludes mailbox email addresses, account IDs,
message IDs, History IDs, run IDs, projection IDs, subjects, bodies, participants and OAuth
credentials.

## Required real History coverage

```text
{event_lines}
```

All four qualified Gmail History event classes were observed at least once.

## Projection convergence

```text
incremental batches: {incremental_batches}
rebuild batches:     {rebuild_batches}
logical digest:      REDACTED / MATCHED
rebuild digest:      REDACTED / MATCHED
result:              PASS
```

The promoted Current State matched a deterministic full rebuild at the same Gmail History frontier.

## Release gates

```text
{check_lines}
```

## Qualification flow

```text
empty isolated state
    ↓
full normalized mailbox snapshot
    ↓
History catch-up
    ↓
initial Current State projection + promotion
    ↓
manual bounded Gmail mutations
    ↓
real incremental History sync
    ↓
incremental projection from previous Current State
    ↓
full rebuild from baseline + complete delta chain
    ↓
logical digest equality
    ↓
projection replay → already current
```

## Release decision

The external live Gmail projection gate for `0.5.0` is satisfied for this candidate.

Stable promotion may continue only if no projection semantics change after this qualification.
Any substantive code change requires returning to a prerelease candidate and repeating the live
gate.
"""


def main() -> int:
    args = _parse_args()
    payload = _read_object(args.input)
    version, qualified_at, observed, checks, projection = _validated(payload)
    content = _markdown(
        version=version,
        qualified_at=qualified_at,
        candidate_commit=args.candidate_commit,
        observed=observed,
        checks=checks,
        projection=projection,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print("redacted_projection_evidence: PASS")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
