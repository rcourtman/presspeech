#!/usr/bin/env python3
"""Require the latest successful main push check for the Pages artifact commit.

The caller queries the repository's check.yml runs filtered by main, push and
the exact checked-out SHA. An incomplete response fails closed instead of
allowing an older success to conceal a newer failure or pending rerun.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

REPOSITORY = "rcourtman/presspeech"
WORKFLOW = ".github/workflows/check.yml"
MAX_BYTES = 2 * 1024 * 1024
MAX_RUNS = 100


def successful_main_check(payload: object, expected_sha: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("expected SHA must be a lowercase 40-character commit ID")
    if not isinstance(payload, dict):
        raise ValueError("workflow response must be an object")
    count, runs = payload.get("total_count"), payload.get("workflow_runs")
    if type(count) is not int or not isinstance(runs, list):
        raise ValueError("workflow response has invalid count or runs")
    if count != len(runs) or not 0 <= count <= MAX_RUNS:
        raise ValueError("workflow response is incomplete or exceeds the bounded run limit")
    matches = []
    seen_ids = set()
    for run in runs:
        if not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0:
            raise ValueError("workflow response contains an invalid run ID")
        if run["id"] in seen_ids:
            raise ValueError("workflow response contains duplicate run IDs")
        seen_ids.add(run["id"])
        if (run.get("head_sha") == expected_sha and run.get("head_branch") == "main"
                and run.get("event") == "push" and run.get("path") == WORKFLOW
                and isinstance(run.get("repository"), dict)
                and run["repository"].get("full_name") == REPOSITORY
                and isinstance(run.get("head_repository"), dict)
                and run["head_repository"].get("full_name") == REPOSITORY):
            matches.append(run)
    if not matches:
        raise ValueError("current main has no matching repository check push run")
    latest = max(matches, key=lambda run: run["id"])
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("current main's latest check push run has not succeeded")
    return latest["id"]


def self_test() -> None:
    sha = "a" * 40
    run = {"id": 10, "head_sha": sha, "head_branch": "main", "event": "push",
           "path": WORKFLOW, "status": "completed", "conclusion": "success",
           "repository": {"full_name": REPOSITORY},
           "head_repository": {"full_name": REPOSITORY}}

    def payload(*runs):
        return {"total_count": len(runs), "workflow_runs": list(runs)}

    def rejected(value, expected=sha):
        try:
            successful_main_check(value, expected)
        except ValueError:
            return
        raise AssertionError("invalid Pages admission fixture was accepted")

    assert successful_main_check(payload(run), sha) == 10
    # A release for an older ancestor may refresh current main, but the older
    # tag's green run cannot substitute for current main's green push run.
    old = dict(run, head_sha="b" * 40, id=9)
    assert successful_main_check(payload(old, run), sha) == 10
    rejected(payload(old))
    for updates in ({"status": "in_progress", "conclusion": None},
                    {"conclusion": "failure"}, {"conclusion": "cancelled"}):
        rejected(payload(run, dict(run, id=11, **updates)))
    for key, value in (("head_sha", "b" * 40), ("head_branch", "feature"),
                       ("event", "pull_request"), ("event", "release"),
                       ("path", ".github/workflows/other.yml"),
                       ("repository", {"full_name": "fork/presspeech"}),
                       ("head_repository", {"full_name": "fork/presspeech"})):
        rejected(payload(dict(run, **{key: value})))
    rejected({"total_count": 101, "workflow_runs": [run]})
    rejected({"total_count": 0, "workflow_runs": [run]})
    rejected({"total_count": True, "workflow_runs": [run]})
    rejected(payload(run, run))
    rejected(payload(dict(run, id=True)))
    rejected(payload(dict(run, id="10")))
    rejected(payload())
    rejected(None)
    rejected(payload(run), sha + "\n")
    rerun = copy.deepcopy(run)
    rerun.update(status="in_progress", conclusion=None, run_attempt=2)
    rejected(payload(rerun))
    print("Pages deployment check self-test passed (23 admission scenarios).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--expected-sha")
    parser.add_argument("--runs-json", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.expected_sha or args.runs_json is None:
        parser.error("--expected-sha and --runs-json are required")
    try:
        with args.runs_json.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("workflow response exceeds the byte limit")
        run_id = successful_main_check(json.loads(raw), args.expected_sha)
    except (OSError, ValueError) as exc:
        print(f"Pages deployment refused: {exc}", file=sys.stderr)
        return 1
    print(f"Pages commit {args.expected_sha} passed main push check run {run_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
