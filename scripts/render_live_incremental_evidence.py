#!/usr/bin/env python
"""Render redacted stable-release evidence from a successful live qualification JSON."""

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
            "Validate a Gmail Ingestion 0.4.0 live qualification JSON and render "
            "redacted stable release evidence."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("qualification/v0.4.0-live-evidence.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/releases/v0.4.0-evidence.md"),
    )
    parser.add_argument(
        "--candidate-commit",
        required=True,
        help="Git commit SHA of the live-qualified stable candidate.",
    )
    return parser.parse_args()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read live qualification evidence {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Live qualification evidence must contain a JSON object")
    return payload


def _validated(payload: dict[str, Any]) -> tuple[str, str, dict[str, int], dict[str, bool]]:
    if payload.get("schema_version") != 1:
        raise SystemExit("Unsupported live qualification evidence schema")
    if payload.get("live_gate_passed") is not True:
        raise SystemExit("Live qualification did not pass")
    if payload.get("stable_eligible") is not True:
        raise SystemExit("Live qualification is not stable-eligible")

    required = payload.get("required_event_types")
    if not isinstance(required, list) or set(required) != set(_EVENT_TYPES):
        raise SystemExit("Stable evidence requires all four Gmail History event types")

    observed_raw = payload.get("observed_event_types")
    if not isinstance(observed_raw, dict):
        raise SystemExit("Live qualification is missing observed event counts")
    observed: dict[str, int] = {}
    for event_type in _EVENT_TYPES:
        value = observed_raw.get(event_type)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SystemExit(f"Stable evidence requires observed {event_type} >= 1")
        observed[event_type] = value

    checks_raw = payload.get("checks")
    if not isinstance(checks_raw, dict) or not checks_raw:
        raise SystemExit("Live qualification is missing checks")
    checks: dict[str, bool] = {}
    for key, value in checks_raw.items():
        if not isinstance(key, str) or not isinstance(value, bool):
            raise SystemExit("Live qualification checks must be boolean")
        checks[key] = value
    failed = sorted(key for key, value in checks.items() if not value)
    if failed:
        raise SystemExit(f"Live qualification contains failed checks: {failed}")

    version = payload.get("qualified_version")
    qualified_at = payload.get("qualified_at")
    if version not in {"0.4.0rc1", "0.4.0"}:
        raise SystemExit(f"Unexpected qualified version: {version!r}")
    if not isinstance(qualified_at, str) or not qualified_at:
        raise SystemExit("Live qualification timestamp is missing")
    return version, qualified_at, observed, checks


def _markdown(
    *,
    version: str,
    qualified_at: str,
    candidate_commit: str,
    observed: dict[str, int],
    checks: dict[str, bool],
) -> str:
    event_lines = "\n".join(
        f"{event_type:<18} {observed[event_type]}"
        for event_type in _EVENT_TYPES
    )
    check_lines = "\n".join(
        f"{key:<42} PASS"
        for key in sorted(checks)
    )
    return f"""# Gmail Ingestion 0.4.0 — Live Gmail Qualification Evidence

## Status

**PASS**

The real Gmail OAuth/API-client incremental path was exercised from:

```text
candidate commit: {candidate_commit}
qualified version: {version}
qualified at:      {qualified_at}
```

This document is intentionally redacted. It does not include mailbox email addresses, message IDs,
History IDs, run IDs, message subjects, message bodies, sender/recipient addresses, OAuth tokens,
refresh tokens or client secrets.

## Required real History coverage

```text
{event_lines}
```

All four stable event types were observed at least once.

## Release gates

```text
{check_lines}
```

## Qualification flow

```text
empty isolated checkpoint root
    ↓
Full Snapshot recovery
    ↓
bootstrap checkpoint at snapshot.history_start
    ↓
History catch-up
    ↓
manual bounded Gmail mutations
    ↓
real History incremental processing
    ↓
same-run recovery
    ↓
cross-run durable receipt replay
```

The cross-run replay reused the existing receipt store and did not rehydrate Gmail messages.

## Release decision

The external live Gmail gate for `0.4.0` is satisfied for this candidate.

Stable promotion may continue only if no code semantics change after this qualification. Any
substantive code change requires returning to a prerelease candidate and repeating the live gate.
"""


def main() -> int:
    args = _parse_args()
    payload = _read_object(args.input)
    version, qualified_at, observed, checks = _validated(payload)
    content = _markdown(
        version=version,
        qualified_at=qualified_at,
        candidate_commit=args.candidate_commit,
        observed=observed,
        checks=checks,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print("redacted_evidence: PASS")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
