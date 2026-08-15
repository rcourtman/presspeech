#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
client="${repo_root}/infra/maintainer-vm/bin/presspeech-mac-release"
worker="${repo_root}/infra/maintainer-vm/macos/presspeech-release-worker"
plist="${repo_root}/infra/maintainer-vm/macos/com.local.presspeech.maintainer-release.plist"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/presspeech-release-queue-self-test.XXXXXX")"
trap 'rm -rf -- "${tmpdir}"' EXIT
state_root="${tmpdir}/Application Support/state"
request_root="${state_root}/requests"
mkdir -p "${request_root}"

# Exercise argument mapping with the worker's own fake ship script. In
# particular, patch must pass zero arguments: an empty Bash array expands as
# an unbound variable under the Bash 3.2 still shipped by macOS.
"${worker}" --self-test >/dev/null

# Unsupported actions exercise queue consumption and atomic result publication
# without contacting GitHub or touching either release checkout.
printf 'unsupported\n' >"${request_root}/20260814T070000Z-2.request"
printf 'unsupported\n' >"${request_root}/20260814T070000Z-1.request"

set +e
PRESSPEECH_RELEASE_STATE_ROOT="${state_root}" "${worker}" >/dev/null 2>&1
first_status=$?
set -e
[[ ${first_status} -eq 2 ]]
grep -qx 'exit_code=2' <(head -1 "${state_root}/20260814T070000Z-1.result")
[[ -f "${request_root}/20260814T070000Z-2.request" ]]

set +e
PRESSPEECH_RELEASE_STATE_ROOT="${state_root}" "${worker}" >/dev/null 2>&1
second_status=$?
set -e
[[ ${second_status} -eq 2 ]]
grep -qx 'exit_code=2' <(head -1 "${state_root}/20260814T070000Z-2.result")
[[ -z "$(find "${request_root}" -mindepth 1 -maxdepth 1 -print -quit)" ]]

# A malformed file must leave the watched directory so it cannot create a
# launchd restart loop.
printf 'unsupported\n' >"${request_root}/malformed.request"
set +e
PRESSPEECH_RELEASE_STATE_ROOT="${state_root}" "${worker}" >/dev/null 2>&1
malformed_status=$?
set -e
[[ ${malformed_status} -eq 2 ]]
[[ -z "$(find "${request_root}" -mindepth 1 -maxdepth 1 -print -quit)" ]]
compgen -G "${state_root}/rejected-*" >/dev/null

python3 - "${plist}" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    payload = plistlib.load(handle)

expected = (
    "__HOME__/Library/Application Support/PresspeechMaintainer/"
    "releases/requests"
)
if payload.get("QueueDirectories") != [expected]:
    raise SystemExit("release LaunchAgent is not watching the request queue")
PY

grep -Fq 'request_root="${state_root}/requests"' "${client}"
if grep -Fq 'launchctl kickstart' "${client}"; then
  echo "Release client must not terminate or restart the active worker." >&2
  exit 1
fi

echo "Maintainer release queue self-test passed."
