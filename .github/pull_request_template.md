## Summary

Describe the problem and why this change is the right fit for Presspeech.

## Verification

List the checks you ran and the platform or hardware used. Include before and
after measurements for transcription-quality, latency, power, or packaging
changes.

## Checklist

- [ ] The change stays local-only and adds no telemetry, analytics, accounts,
      cloud transcription, or undocumented network calls.
- [ ] Transcript content is never written to logs.
- [ ] macOS changes preserve the Swift concurrency, audio-converter, TCC,
      entitlements, and resource-bundling invariants documented in `AGENTS.md`.
- [ ] Windows changes preserve the model-readiness, epoch-guarded timer, TDT
      duration, updater-integrity, and packaging invariants documented in
      `AGENTS.md`.
- [ ] Relevant self-tests and native QA have passed, or the verification gap is
      explained above.
- [ ] User-facing behavior and privacy documentation are updated where needed.
