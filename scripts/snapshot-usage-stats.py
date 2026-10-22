#!/usr/bin/env python3
"""Snapshot public usage signals into a CSV history.

Presspeech has no telemetry. GitHub already keeps a few public signals, but
discards most of them: release download counts are cumulative only, and repo
traffic covers just the last 14 days. The weekly usage-stats workflow runs this
script against a checkout of the `stats` branch so the history accumulates.

Files written to the data directory:

  downloads.csv       snapshot_date,tag,platform,asset,kind,download_count
  traffic-views.csv   day,count,uniques   (rcourtman/presspeech)
  traffic-clones.csv  day,count,uniques   (rcourtman/presspeech)
  tap-clones.csv      day,count,uniques   (rcourtman/homebrew-presspeech)
  traffic-totals.csv  snapshot_date,repo,metric,count,uniques  (14-day window)
  referrers.csv       snapshot_date,referrer,count,uniques
  paths.csv           snapshot_date,path,title,count,uniques

Per-day traffic overlaps between weekly runs, so rows merge by day and the
latest fetch wins. Snapshot files replace every row for the run's date, so a
rerun on the same day is idempotent.

Release downloads use GITHUB_TOKEN. Traffic needs STATS_TOKEN (a fine-grained
PAT with Administration read on both repos); without it traffic is skipped.
`--via-gh` fetches through the local `gh` CLI instead, for maintainer runs.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

REPOSITORY = "rcourtman/presspeech"
TAP_REPOSITORY = "rcourtman/homebrew-presspeech"
API = "https://api.github.com"
MAX_RELEASE_PAGES = 20

DOWNLOAD_FIELDS = ["snapshot_date", "tag", "platform", "asset", "kind", "download_count"]
DAILY_FIELDS = ["day", "count", "uniques"]
TOTAL_FIELDS = ["snapshot_date", "repo", "metric", "count", "uniques"]
REFERRER_FIELDS = ["snapshot_date", "referrer", "count", "uniques"]
PATH_FIELDS = ["snapshot_date", "path", "title", "count", "uniques"]

Fetch = Callable[[str], object]


class Unavailable(Exception):
    """An endpoint refused the request (missing scope, expired token)."""


def urllib_fetcher(token: str | None) -> Fetch:
    def fetch(path: str) -> object:
        request = urllib.request.Request(f"{API}/{path}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "presspeech-usage-stats")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404):
                raise Unavailable(f"{path}: HTTP {error.code}") from error
            raise

    return fetch


def gh_fetcher(path: str) -> object:
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise Unavailable(f"{path}: {result.stderr.strip() or 'gh api failed'}")
    return json.loads(result.stdout)


# --- collection ------------------------------------------------------------


def classify(tag: str, asset: str) -> tuple[str, str]:
    platform = "windows" if tag.startswith("windows-") or asset.endswith(".exe") else "macos"
    kind = "checksum" if asset.endswith(".sha256") else "installer"
    return platform, kind


def collect_downloads(fetch: Fetch, snapshot_date: str) -> list[dict[str, str]]:
    rows = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        releases = fetch(f"repos/{REPOSITORY}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError("releases response must be a list")
        for release in releases:
            if release.get("draft"):
                continue
            tag = release["tag_name"]
            for asset in release.get("assets", []):
                platform, kind = classify(tag, asset["name"])
                rows.append({
                    "snapshot_date": snapshot_date,
                    "tag": tag,
                    "platform": platform,
                    "asset": asset["name"],
                    "kind": kind,
                    "download_count": str(asset["download_count"]),
                })
        if len(releases) < 100:
            return rows
    raise ValueError(f"more than {MAX_RELEASE_PAGES} pages of releases")


def daily_rows(payload: object, key: str) -> list[dict[str, str]]:
    return [
        {
            "day": entry["timestamp"][:10],
            "count": str(entry["count"]),
            "uniques": str(entry["uniques"]),
        }
        for entry in payload[key]
    ]


def total_row(snapshot_date: str, repo: str, metric: str, payload: dict) -> dict[str, str]:
    return {
        "snapshot_date": snapshot_date,
        "repo": repo,
        "metric": metric,
        "count": str(payload["count"]),
        "uniques": str(payload["uniques"]),
    }


def collect_traffic(fetch: Fetch, snapshot_date: str) -> dict[str, list[dict[str, str]]]:
    views = fetch(f"repos/{REPOSITORY}/traffic/views?per=day")
    clones = fetch(f"repos/{REPOSITORY}/traffic/clones?per=day")
    tap = fetch(f"repos/{TAP_REPOSITORY}/traffic/clones?per=day")
    referrers = fetch(f"repos/{REPOSITORY}/traffic/popular/referrers")
    paths = fetch(f"repos/{REPOSITORY}/traffic/popular/paths")
    return {
        "traffic-views.csv": daily_rows(views, "views"),
        "traffic-clones.csv": daily_rows(clones, "clones"),
        "tap-clones.csv": daily_rows(tap, "clones"),
        "traffic-totals.csv": [
            total_row(snapshot_date, REPOSITORY, "views", views),
            total_row(snapshot_date, REPOSITORY, "clones", clones),
            total_row(snapshot_date, TAP_REPOSITORY, "clones", tap),
        ],
        "referrers.csv": [
            {
                "snapshot_date": snapshot_date,
                "referrer": entry["referrer"],
                "count": str(entry["count"]),
                "uniques": str(entry["uniques"]),
            }
            for entry in referrers
        ],
        "paths.csv": [
            {
                "snapshot_date": snapshot_date,
                "path": entry["path"],
                "title": entry["title"],
                "count": str(entry["count"]),
                "uniques": str(entry["uniques"]),
            }
            for entry in paths
        ],
    }


# --- merging ---------------------------------------------------------------


def merge_daily(existing: list[dict], new: list[dict]) -> list[dict]:
    """Union by day; a newer fetch replaces the older value for that day."""
    by_day = {row["day"]: row for row in existing}
    by_day.update({row["day"]: row for row in new})
    return [by_day[day] for day in sorted(by_day)]


def merge_snapshot(existing: list[dict], new: list[dict], fields: list[str]) -> list[dict]:
    """Replace every row from the new snapshot's date, keep older snapshots."""
    dates = {row["snapshot_date"] for row in new}
    kept = [row for row in existing if row["snapshot_date"] not in dates]
    key_fields = [f for f in fields if f not in ("count", "uniques", "download_count")]
    return sorted(kept + new, key=lambda row: tuple(row[f] for f in key_fields))


SNAPSHOT_FIELDS = {
    "downloads.csv": DOWNLOAD_FIELDS,
    "traffic-totals.csv": TOTAL_FIELDS,
    "referrers.csv": REFERRER_FIELDS,
    "paths.csv": PATH_FIELDS,
}


def fields_for(name: str) -> list[str]:
    return SNAPSHOT_FIELDS.get(name, DAILY_FIELDS)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def render_csv(rows: list[dict], fields: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def merge_into(data_dir: Path, name: str, new: list[dict]) -> bool:
    """Merge rows into one CSV; return whether its contents changed."""
    path = data_dir / name
    fields = fields_for(name)
    existing = read_csv(path)
    if name in SNAPSHOT_FIELDS:
        merged = merge_snapshot(existing, new, fields)
    else:
        merged = merge_daily(existing, new)
    text = render_csv(merged, fields)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return True


def notice(level: str, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title=usage stats::{message}")
    else:
        print(f"{level}: {message}")


def snapshot(
    data_dir: Path,
    snapshot_date: str,
    release_fetch: Fetch,
    traffic_fetch: Fetch | None,
) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    changed = []
    if merge_into(data_dir, "downloads.csv", collect_downloads(release_fetch, snapshot_date)):
        changed.append("downloads.csv")
    status = 0
    if traffic_fetch is None:
        notice("notice", "STATS_TOKEN is not set; skipped repo and tap traffic.")
    else:
        try:
            traffic = collect_traffic(traffic_fetch, snapshot_date)
        except Unavailable as error:
            # Keep the download snapshot, but fail so an expired or
            # under-scoped token is noticed rather than silently ignored.
            notice("error", f"traffic unavailable, check STATS_TOKEN: {error}")
            status = 1
        else:
            for name, rows in traffic.items():
                if merge_into(data_dir, name, rows):
                    changed.append(name)
    print(f"changed: {', '.join(changed) if changed else 'nothing'}")
    return status


# --- summary ---------------------------------------------------------------


def installer_totals(rows: list[dict], date: str) -> dict[str, int]:
    totals = {"macos": 0, "windows": 0}
    for row in rows:
        if row["snapshot_date"] == date and row["kind"] == "installer":
            totals[row["platform"]] += int(row["download_count"])
    return totals


def weekly_sums(rows: list[dict], end: dt.date, weeks: int = 4) -> list[str]:
    """Format count/uniques per week ending at `end`; "-" where nothing was recorded."""
    buckets: list[tuple[int, int] | None] = [None] * weeks
    for row in rows:
        age = (end - dt.date.fromisoformat(row["day"])).days
        if 0 <= age < weeks * 7:
            index = weeks - 1 - age // 7
            count, uniques = buckets[index] or (0, 0)
            buckets[index] = (count + int(row["count"]), uniques + int(row["uniques"]))
    return [f"{bucket[0]}/{bucket[1]}" if bucket else "-" for bucket in buckets]


def summary(data_dir: Path) -> str:
    lines = [f"Usage stats in {data_dir}"]
    downloads = read_csv(data_dir / "downloads.csv")
    dates = sorted({row["snapshot_date"] for row in downloads})
    if not dates:
        lines.append("No download snapshots yet.")
    else:
        latest = dates[-1]
        totals = installer_totals(downloads, latest)
        lines.append("")
        lines.append(f"Installer downloads, all releases (snapshot {latest}; checksums excluded)")
        for platform, label in (("macos", "macOS"), ("windows", "Windows")):
            releases = [
                row for row in downloads
                if row["snapshot_date"] == latest and row["platform"] == platform
                and row["kind"] == "installer"
            ]
            newest = max(releases, key=lambda row: version_key(row["tag"]), default=None)
            newest_text = f"; newest {newest['tag']}: {newest['download_count']}" if newest else ""
            lines.append(f"  {label:<8}{totals[platform]:>6}{newest_text}")
        lines.append("  Trend (last 5 snapshots, change since previous):")
        previous = None
        recent = dates[-5:]
        if len(dates) > 5:
            previous = installer_totals(downloads, dates[-6])
        for date in recent:
            current = installer_totals(downloads, date)
            parts = []
            for platform, label in (("macos", "macOS"), ("windows", "Windows")):
                delta = f" ({current[platform] - previous[platform]:+d})" if previous else ""
                parts.append(f"{label} {current[platform]}{delta}")
            lines.append(f"    {date}  " + "  ".join(parts))
            previous = current

    series = [
        ("presspeech views", "traffic-views.csv"),
        ("presspeech clones", "traffic-clones.csv"),
        ("tap clones", "tap-clones.csv"),
    ]
    loaded = [(label, read_csv(data_dir / name)) for label, name in series]
    days = [row["day"] for _, rows in loaded for row in rows]
    lines.append("")
    if not days:
        lines.append("No traffic recorded yet (STATS_TOKEN not configured?).")
        return "\n".join(lines)
    end = dt.date.fromisoformat(max(days))
    starts = [end - dt.timedelta(days=7 * (3 - i) + 6) for i in range(4)]
    header = "".join(f"{start.strftime('%b %d'):>12}" for start in starts)
    lines.append(f"Traffic per week (count/sum of daily uniques), weeks to {end}")
    lines.append(f"  {'week from':<18}{header}")
    for label, rows in loaded:
        cells = "".join(f"{cell:>12}" for cell in weekly_sums(rows, end))
        lines.append(f"  {label:<18}{cells}")

    totals = read_csv(data_dir / "traffic-totals.csv")
    if not totals:
        return "\n".join(lines)
    latest = max(row["snapshot_date"] for row in totals)
    lines.append(f"  14-day window at {latest}:")
    for row in totals:
        if row["snapshot_date"] == latest:
            lines.append(
                f"    {row['repo']} {row['metric']}: {row['count']} ({row['uniques']} unique)"
            )
    for name, key, label in (
        ("referrers.csv", "referrer", "Top referrers"),
        ("paths.csv", "path", "Top paths"),
    ):
        top = sorted(
            (row for row in read_csv(data_dir / name) if row["snapshot_date"] == latest),
            key=lambda row: -int(row["count"]),
        )[:5]
        lines.append(f"  {label}: " + (", ".join(
            f"{row[key]} {row['count']}/{row['uniques']}" for row in top
        ) or "none"))
    return "\n".join(lines)


def version_key(tag: str) -> tuple[int, ...]:
    digits = tag.rsplit("v", 1)[-1]
    return tuple(int(part) if part.isdigit() else 0 for part in digits.split("."))


# --- self-test -------------------------------------------------------------


def fake_fetcher(responses: dict[str, object]) -> Fetch:
    def fetch(path: str) -> object:
        for prefix, payload in responses.items():
            if path.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected fetch {path}")

    return fetch


def traffic_payload(key: str, start: dt.date, counts: list[int]) -> dict:
    entries = [
        {
            "timestamp": f"{start + dt.timedelta(days=i)}T00:00:00Z",
            "count": count,
            "uniques": max(count - 1, 0),
        }
        for i, count in enumerate(counts)
    ]
    return {
        "count": sum(counts),
        "uniques": sum(e["uniques"] for e in entries),
        key: entries,
    }


def run_self_test() -> None:
    def check(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(f"self-test: {message}")

    check(classify("v0.3.10", "Presspeech.zip") == ("macos", "installer"), "macOS zip")
    check(classify("v0.3.10", "Presspeech.zip.sha256") == ("macos", "checksum"), "checksum")
    check(
        classify("windows-v0.1.12", "Presspeech-Setup-0.1.12-x64.exe") == ("windows", "installer"),
        "Windows exe",
    )
    check(version_key("windows-v0.1.12") > version_key("windows-v0.1.9"), "version order")

    releases = [
        {"tag_name": "v0.3.10", "draft": False, "assets": [
            {"name": "Presspeech.zip", "download_count": 3},
            {"name": "Presspeech.zip.sha256", "download_count": 5},
        ]},
        {"tag_name": "windows-v0.1.12", "draft": False, "assets": [
            {"name": "Presspeech-Setup-0.1.12-x64.exe", "download_count": 6},
        ]},
        {"tag_name": "v9.9.9", "draft": True, "assets": [
            {"name": "Presspeech.zip", "download_count": 0},
        ]},
    ]
    week1 = dt.date(2026, 10, 1)
    first = {
        f"repos/{REPOSITORY}/releases": releases,
        f"repos/{REPOSITORY}/traffic/views": traffic_payload("views", week1, [5] * 14),
        f"repos/{REPOSITORY}/traffic/clones": traffic_payload("clones", week1, [2] * 14),
        f"repos/{TAP_REPOSITORY}/traffic/clones": traffic_payload("clones", week1, [3] * 14),
        f"repos/{REPOSITORY}/traffic/popular/referrers": [
            {"referrer": "github.com", "count": 4, "uniques": 2},
        ],
        f"repos/{REPOSITORY}/traffic/popular/paths": [
            {"path": "/rcourtman/presspeech", "title": "Overview", "count": 9, "uniques": 4},
        ],
    }

    with tempfile.TemporaryDirectory() as temporary:
        data = Path(temporary)
        fetch = fake_fetcher(first)
        check(snapshot(data, "2026-10-14", fetch, fetch) == 0, "first snapshot failed")
        before = {path.name: path.read_bytes() for path in data.iterdir()}
        check(len(before) == 7, f"expected 7 CSVs, got {sorted(before)}")
        check(
            [row["asset"] for row in read_csv(data / "downloads.csv")] == [
                "Presspeech.zip", "Presspeech.zip.sha256", "Presspeech-Setup-0.1.12-x64.exe",
            ],
            "draft releases must be skipped and rows sorted",
        )

        # Rerunning the same day with the same data changes nothing.
        snapshot(data, "2026-10-14", fetch, fetch)
        after = {path.name: path.read_bytes() for path in data.iterdir()}
        check(before == after, "same-day rerun was not idempotent")

        # A week later the 14-day window overlaps by 7 days; the newest
        # values win for overlapping days and older days are kept.
        week2 = week1 + dt.timedelta(days=7)
        second = dict(first)
        second[f"repos/{REPOSITORY}/traffic/views"] = traffic_payload("views", week2, [7] * 14)
        second[f"repos/{REPOSITORY}/releases"] = [
            {**releases[0], "assets": [{"name": "Presspeech.zip", "download_count": 10}]},
        ]
        second[f"repos/{REPOSITORY}/traffic/popular/referrers"] = []
        fetch = fake_fetcher(second)
        snapshot(data, "2026-10-21", fetch, fetch)
        views = read_csv(data / "traffic-views.csv")
        check(len(views) == 21, f"expected 21 distinct days, got {len(views)}")
        check(len({row["day"] for row in views}) == len(views), "duplicate days")
        check([row["day"] for row in views] == sorted(row["day"] for row in views), "unsorted")
        counts = {row["day"]: row["count"] for row in views}
        check(counts["2026-10-01"] == "5", "day outside the new window was lost")
        check(counts["2026-10-08"] == "7", "overlapping day kept the stale value")
        downloads = read_csv(data / "downloads.csv")
        check(
            {row["snapshot_date"] for row in downloads} == {"2026-10-14", "2026-10-21"},
            "download snapshots were not both kept",
        )
        referrers = read_csv(data / "referrers.csv")
        check(
            [row["snapshot_date"] for row in referrers] == ["2026-10-14"],
            "earlier referrer snapshot should remain, empty one adds nothing",
        )

        text = summary(data)
        check("Presspeech.zip" not in text, "summary should report totals, not assets")
        check("macOS       10; newest v0.3.10: 10" in text, f"summary totals wrong:\n{text}")
        check("2026-10-21  macOS 10 (+7)  Windows 0 (-6)" in text, f"trend wrong:\n{text}")
        check(
            "presspeech views - 35/28 49/42 49/42" in " ".join(text.split()),
            f"weekly traffic wrong:\n{text}",
        )
        check("Top referrers: none" in text, "stale referrer snapshot shown as current")
        check("Top paths: /rcourtman/presspeech 9/4" in text, "summary lacks paths")

    with tempfile.TemporaryDirectory() as temporary:
        data = Path(temporary)
        release_only = fake_fetcher({f"repos/{REPOSITORY}/releases": releases})
        check(snapshot(data, "2026-10-14", release_only, None) == 0, "no-token run must pass")
        check(sorted(p.name for p in data.iterdir()) == ["downloads.csv"], "traffic written")
        check("No traffic recorded yet" in summary(data), "summary without traffic")

        refused = fake_fetcher({
            f"repos/{REPOSITORY}/releases": releases,
            "repos/": Unavailable("HTTP 403"),
        })
        check(snapshot(data, "2026-10-15", release_only, refused) == 1, "403 must fail")
        dates = {row["snapshot_date"] for row in read_csv(data / "downloads.csv")}
        check("2026-10-15" in dates, "downloads must be kept when traffic fails")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true", help="run offline tests")
    mode.add_argument("--snapshot", type=Path, metavar="DIR", help="fetch and merge into DIR")
    mode.add_argument("--summary", type=Path, metavar="DIR", help="print totals and trend")
    parser.add_argument(
        "--via-gh", action="store_true",
        help="fetch through the local gh CLI (releases and traffic) instead of tokens",
    )
    args = parser.parse_args()

    if args.self_test:
        with contextlib.redirect_stdout(io.StringIO()):
            run_self_test()
        print("usage stats self-test passed")
        return 0
    if args.summary:
        print(summary(args.summary))
        return 0

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if args.via_gh:
        return snapshot(args.snapshot, today, gh_fetcher, gh_fetcher)
    stats_token = os.environ.get("STATS_TOKEN") or None
    return snapshot(
        args.snapshot,
        today,
        urllib_fetcher(os.environ.get("GITHUB_TOKEN") or None),
        urllib_fetcher(stats_token) if stats_token else None,
    )


if __name__ == "__main__":
    sys.exit(main())
