#!/usr/bin/env python3
"""Versioned rescoring diagnostics beside the unchanged vocabulary metric TSV."""
from __future__ import annotations
import argparse
import csv
import json
import re
import tempfile
from pathlib import Path

POLICIES = ('v3-vocab', 'v3-vocab-conservative', 'v3-vocab-no-rescue',
            'v3-vocab-exact-similarity', 'sliding-vocab',
            'sliding-vocab-conservative', 'sliding-vocab-no-rescue')
OUTCOMES = ('succeeded', 'failed', 'skipped', 'unobservable')
RECORD = re.compile(r'rescoring trial=(\d+)/(\d+) attempted=(0|1|unknown) '
                    r'succeeded=([01]) failed=([01]) skipped=([01]) unobservable=([01])')


def read_trials(text: str, expected: int) -> tuple[list[dict], list[str]]:
    records, errors = [], []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('rescoring '):
            continue
        match = RECORD.fullmatch(line)
        if not match:
            errors.append('malformed rescoring record')
            continue
        trial, total = int(match[1]), int(match[2])
        attempt = None if match[3] == 'unknown' else int(match[3])
        counts = dict(zip(OUTCOMES, map(int, match.groups()[3:])))
        if trial in seen or not 1 <= trial <= expected or total != expected:
            errors.append('duplicate or inconsistent rescoring trial')
            continue
        seen.add(trial)
        if sum(counts.values()) != 1 or (
            (counts['succeeded'] or counts['failed']) and attempt != 1
        ) or (counts['skipped'] and attempt != 0):
            errors.append('inconsistent rescoring outcome')
            continue
        records.append(dict(trial=trial, attempted=attempt, **counts))
    if len(records) != expected:
        errors.append('incomplete rescoring trial records')
    return sorted(records, key=lambda row: row['trial']), sorted(set(errors))


def collect(tsv: Path, logs: Path, trials: int) -> dict:
    if trials < 1:
        raise ValueError('trials must be positive')
    with tsv.open() as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        if not {'clip_id', 'variant'}.issubset(reader.fieldnames or []):
            raise ValueError('metric TSV lacks clip and variant identifiers')
        rows = list(reader)
    result = {'schema_version': 1, 'measured_trials_per_clip': trials, 'policies': {}}
    seen = set()
    for row in rows:
        policy, clip = row['variant'], row['clip_id']
        if policy not in POLICIES:
            continue
        if not re.fullmatch(r'[pnx]\d+', clip):
            raise ValueError('invalid vocabulary clip identifier')
        if (clip, policy) in seen:
            raise ValueError('duplicate clip/policy metric row')
        seen.add((clip, policy))
        entry = result['policies'].setdefault(policy, {'clips': {}, 'attempted': 0,
            'unknown_attempts': 0, **dict.fromkeys(OUTCOMES, 0)})
        path = logs / f'{clip}-{policy}.bench.txt'
        if path.is_file():
            records, errors = read_trials(path.read_text(), trials)
        else:
            records, errors = [], ['missing rescoring log']
        entry['clips'][clip] = {'trials': records, 'errors': errors}
        for record in records:
            if record['attempted'] is None:
                entry['unknown_attempts'] += 1
            else:
                entry['attempted'] += record['attempted']
            for outcome in OUTCOMES:
                entry[outcome] += record[outcome]
    for entry in result['policies'].values():
        errors = {error for clip in entry['clips'].values() for error in clip['errors']}
        for outcome in ('failed', 'skipped', 'unobservable'):
            if entry[outcome]:
                errors.add(f'{entry[outcome]} {outcome} rescoring outcomes')
        if entry['unknown_attempts']:
            errors.add(f"{entry['unknown_attempts']} unobservable rescoring attempts")
        entry['blockers'] = sorted(errors)
        entry['verified'] = bool(entry['clips']) and not errors
    return result


def blockers(evidence: dict, policy: str) -> list[str]:
    if evidence.get('schema_version') != 1:
        raise ValueError('unsupported rescoring evidence schema')
    entry = evidence.get('policies', {}).get(policy)
    if not entry or not entry.get('clips'):
        return ['rescoring evidence unavailable']
    return entry['blockers']


def markdown(evidence: dict) -> str:
    lines = ['## Rescoring execution evidence', '',
        'Counts cover measured trials only; warmup is excluded. Succeeded means the evaluation completed, not that it changed text. SDK nil conflates no change with skipped or failed evaluation and is unobservable. Sliding internals do not expose whether rescoring was attempted. Missing, failed, skipped or unobservable evidence blocks efficacy qualification; exploratory measurements remain available.', '',
        '| Policy | Known attempts | Unknown attempts | Succeeded | Failed | Skipped | Unobservable | Evidence |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for policy, entry in sorted(evidence['policies'].items()):
        reason = '; '.join(blockers(evidence, policy)) or 'verified'
        values = [entry[key] for key in ('attempted', 'unknown_attempts', *OUTCOMES)]
        lines.append('| `' + policy + '` | ' + ' | '.join(map(str, values)) + ' | ' + reason + ' |')
    return '\n'.join(lines) + '\n'


def self_test() -> None:
    good = 'rescoring trial=1/1 attempted=1 succeeded=1 failed=0 skipped=0 unobservable=0'
    for record in (good, good.replace('succeeded=1 failed=0', 'succeeded=0 failed=1'),
                   good.replace('attempted=1 succeeded=1 failed=0 skipped=0', 'attempted=0 succeeded=0 failed=0 skipped=1'),
                   good.replace('attempted=1 succeeded=1', 'attempted=unknown succeeded=0').replace('unobservable=0', 'unobservable=1')):
        records, errors = read_trials(record, 1)
        assert len(records) == 1 and not errors
    for text in ('', good+'\n'+good, good.replace('1/1', '2/1'), good.replace('succeeded=1', 'succeeded=2'),
                 good.replace('attempted=1', 'attempted=unknown'), good.replace('failed=0', 'failed=1')):
        assert read_trials(text, 1)[1], text
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # Existing 17-column TSV consumers remain valid; outcomes live elsewhere.
        header = 'clip_id variant wer_percent critical_matched critical_total critical_recall_percent critical_unexpected p50_ms peak_mb cache_mb prepare_ms word_errors reference_words critical_precision_percent best_word_errors highest_critical_matched lowest_critical_unexpected'.split()
        row = dict.fromkeys(header, '0'); row.update(clip_id='n001', variant='v3-vocab')
        tsv = root/'results.tsv'
        with tsv.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=header, delimiter='\t'); writer.writeheader(); writer.writerow(row)
        original = tsv.read_bytes(); log = root/'n001-v3-vocab.bench.txt'
        log.write_text(good)
        assert not blockers(collect(tsv, root, 1), 'v3-vocab')
        for outcome in ('failed', 'skipped', 'unobservable'):
            attempt = '0' if outcome == 'skipped' else '1'
            log.write_text(f'rescoring trial=1/1 attempted={attempt} '+ ' '.join(f'{key}={int(key==outcome)}' for key in OUTCOMES))
            evidence = collect(tsv, root, 1)
            assert any(outcome in reason for reason in blockers(evidence, 'v3-vocab'))
            assert not evidence['policies']['v3-vocab']['verified']
        log.write_text('legacy transcript metrics without stage evidence')
        assert 'incomplete rescoring trial records' in blockers(collect(tsv, root, 1), 'v3-vocab')
        log.unlink()
        assert 'missing rescoring log' in blockers(collect(tsv, root, 1), 'v3-vocab')
        assert tsv.read_bytes() == original
        assert blockers(collect(tsv, root, 1), 'v3-vocab-exact-similarity') == ['rescoring evidence unavailable']
    print('rescoring evidence self-test passed')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--tsv', type=Path)
    parser.add_argument('--logs', type=Path)
    parser.add_argument('--trials', type=int)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--blockers', choices=POLICIES)
    parser.add_argument('--markdown', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if args.evidence:
        evidence = json.loads(args.evidence.read_text())
    elif args.tsv and args.logs and args.trials:
        evidence = collect(args.tsv, args.logs, args.trials)
    else:
        parser.error('provide --evidence or --tsv/--logs/--trials')
    if args.blockers:
        print('; '.join(blockers(evidence, args.blockers)))
    elif args.markdown:
        print(markdown(evidence))
    else:
        print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
