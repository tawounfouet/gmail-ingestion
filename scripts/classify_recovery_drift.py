#!/usr/bin/env python3
"""Classify differences between two immutable Gmail recovery captures offline.

Reports only fixed field-group names and aggregate counts. Message IDs, original
field values, headers, addresses, bodies and credential materials are never printed.
Does not contact Gmail or write files.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from compare_recovery_evidence import _manifest
from probe_missing_added_raw_live import unresolved_ids
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

_JOB = "gmail.recovery_evidence"
_SOURCE_GROUPS = frozenset(
    {
        "message_identity",
        "history_id",
        "label_ids",
        "snippet",
        "size_estimate",
        "internal_date",
        "mime_headers",
        "mime_body",
        "mime_structure",
        "other_source_field",
    }
)


def _group(path: tuple[str | int, ...]) -> str:
    if not path:
        return "other_source_field"
    top = path[0]
    fixed = {
        "id": "message_identity",
        "threadId": "message_identity",
        "historyId": "history_id",
        "labelIds": "label_ids",
        "snippet": "snippet",
        "sizeEstimate": "size_estimate",
        "internalDate": "internal_date",
    }
    if top in fixed:
        return fixed[top]  # type: ignore[index]
    if top == "payload":
        if "headers" in path:
            return "mime_headers"
        if "body" in path:
            return "mime_body"
        return "mime_structure"
    return "other_source_field"


def changed_groups(
    before: Any,
    after: Any,
    path: tuple[str | int, ...] = (),
    *,
    depth: int = 0,
) -> set[str]:
    """Classify changed JSON paths, never returning source values or path details."""
    if before == after:
        return set()
    if depth >= 40:
        return {_group(path)}
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        groups: set[str] = set()
        for key in before.keys() | after.keys():
            if not isinstance(key, str):
                return {_group(path)}
            if key not in before or key not in after:
                groups.add(_group((*path, key)))
            else:
                groups.update(
                    changed_groups(
                        before[key], after[key], (*path, key), depth=depth + 1
                    )
                )
        return groups
    if (
        isinstance(before, Sequence) and isinstance(after, Sequence)
        and not isinstance(before, (str, bytes, bytearray))
        and not isinstance(after, (str, bytes, bytearray))
    ):
        if len(before) != len(after):
            return {_group(path)}
        groups = set()
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            groups.update(
                changed_groups(old, new, (*path, index), depth=depth + 1)
            )
        return groups
    return {_group(path)}


def classify(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    first_run_id: UUID,
    second_run_id: UUID,
) -> dict[str, object]:
    """Compare already captured RAW, validating provenance and accounting."""
    if first_run_id == second_run_id:
        raise ValueError("Distinct recovery runs are required")
    ids, historical_events, target = unresolved_ids(
        artifact_root, account_id, baseline_run_id, delta_run_ids
    )
    store = LocalArtifactStore(artifact_root)
    first, first_404, _ = _manifest(
        store, first_run_id, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        expected_ids=ids, target_history_id=target,
    )
    second, second_404, _ = _manifest(
        store, second_run_id, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        expected_ids=ids, target_history_id=target,
    )
    if first != second or first_404 != second_404:
        raise ValueError("Recovery inventories differ; classify only matched captures")

    writer = RawMessageWriter(store, job_id=_JOB)
    category_counts: Counter[str] = Counter()
    exact = changed = 0
    for message_id in sorted(first):
        before = writer.read(first_run_id, message_id)
        after = writer.read(second_run_id, message_id)
        if before == after:
            exact += 1
            continue
        changed += 1
        groups = changed_groups(before, after)
        if not groups or not groups.issubset(_SOURCE_GROUPS):
            raise ValueError("Unclassified RAW source difference")
        category_counts.update(groups)

    return {
        "status": "CLASSIFIED",
        "historical_target_history_id": target,
        "historical_unresolved_add_events": historical_events,
        "compared_captured": len(first),
        "exactly_identical_messages": exact,
        "changed_messages": changed,
        "changed_field_group_message_counts": dict(sorted(category_counts.items())),
        "unchanged_404_inventory_count": len(first_404),
        "source_label_groups_changed": category_counts["label_ids"],
        "source_history_groups_changed": category_counts["history_id"],
        "mime_headers_changed": category_counts["mime_headers"],
        "mime_body_changed": category_counts["mime_body"],
        "normalization_relevance_requires_review": changed > 0,
        "historical_projection_qualified": False,
        "gmail_api_calls": 0,
        "persistent_writes": 0,
        "caveat": (
            "Two current observations never prove Gmail message state at a past "
            "History frontier. Classification is descriptive, not authorization "
            "to repair historical source runs."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", type=UUID, required=True)
    parser.add_argument("--delta-run-id", type=UUID, action="append", required=True)
    parser.add_argument("--first-recovery-run-id", type=UUID, required=True)
    parser.add_argument("--second-recovery-run-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        result = classify(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            first_run_id=args.first_recovery_run_id,
            second_run_id=args.second_recovery_run_id,
        )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"DRIFT CLASSIFICATION FAILED: exception_type={type(exc).__name__}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
