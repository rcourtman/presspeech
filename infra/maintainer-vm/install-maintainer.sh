#!/usr/bin/env bash
set -euo pipefail

target="${1:-presspeech-dev.local}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bundle_root="${repo_root}/infra/maintainer-vm"

rsync -a --delete "${bundle_root}/" "rcourtman@${target}:/tmp/presspeech-maintainer-install/"

ssh "rcourtman@${target}" 'sudo bash -s' <<'REMOTE'
set -euo pipefail
source_root=/tmp/presspeech-maintainer-install

install -d -m 0755 /etc/presspeech-maintainer /usr/local/libexec
install -d -m 0700 -o presspeech-agent -g presspeech-agent /var/lib/presspeech-maintainer/runs
install -m 0644 "${source_root}/config/daily-prompt.txt" /etc/presspeech-maintainer/daily-prompt.txt
install -m 0755 "${source_root}/bin/presspeech-maintainer-run" /usr/local/libexec/presspeech-maintainer-run
install -m 0755 "${source_root}/bin/presspeech-mac-qa" /usr/local/bin/presspeech-mac-qa
install -m 0755 "${source_root}/bin/presspeech-windows-qa" /usr/local/bin/presspeech-windows-qa
install -m 0755 "${source_root}/bin/bootstrap-windows-qa" /usr/local/bin/bootstrap-windows-qa
install -m 0755 "${source_root}/bin/presspeech-maintainer-commission" /usr/local/bin/presspeech-maintainer-commission
install -m 0644 "${source_root}/systemd/presspeech-maintainer.service" /etc/systemd/system/presspeech-maintainer.service
install -m 0644 "${source_root}/systemd/presspeech-maintainer.timer" /etc/systemd/system/presspeech-maintainer.timer

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

sudo -u presspeech-agent -H bash -s <<'AGENT'
set -euo pipefail
export HOME=/var/lib/presspeech-agent
if [[ ! -x "${HOME}/.local/bin/mise" ]]; then
  curl https://mise.run | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"
"${HOME}/.local/bin/mise" use --global node@lts
"${HOME}/.local/bin/mise" exec -- npm install --global @openai/codex
AGENT

cat >/usr/local/bin/codex <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -un)" != "presspeech-agent" ]]; then
  echo "The unattended Codex runtime belongs to presspeech-agent." >&2
  exit 126
fi
export HOME=/var/lib/presspeech-agent
export CODEX_HOME=/var/lib/presspeech-agent/codex
exec "${HOME}/.local/bin/mise" exec -- codex "$@"
LAUNCHER
chmod 0755 /usr/local/bin/codex

install -d -m 0700 -o presspeech-agent -g presspeech-agent /var/lib/presspeech-agent/.ssh
mac_qa_key=/var/lib/presspeech-agent/.ssh/id_ed25519_mac_qa
if [[ ! -f "${mac_qa_key}" ]]; then
  sudo -u presspeech-agent -H ssh-keygen \
    -q -t ed25519 -N '' \
    -C 'presspeech-dev Mac QA' \
    -f "${mac_qa_key}"
fi
windows_qa_key=/var/lib/presspeech-agent/.ssh/id_ed25519_windows_qa
if [[ ! -f "${windows_qa_key}" ]]; then
  sudo -u presspeech-agent -H ssh-keygen \
    -q -t ed25519 -N '' \
    -C 'presspeech-dev Windows QA' \
    -f "${windows_qa_key}"
fi
cat >/var/lib/presspeech-agent/.ssh/config <<'SSH_CONFIG'
Host presspeech-mac-qa
  HostName 192.168.0.113
  User rcourtman
  IdentityFile /var/lib/presspeech-agent/.ssh/id_ed25519_mac_qa
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking accept-new

Host presspeech-windows-qa
  HostName win11-pvepc.mist-stork.ts.net
  User winadmin
  IdentityFile /var/lib/presspeech-agent/.ssh/id_ed25519_windows_qa
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking accept-new
  ProxyJump presspeech-mac-qa
SSH_CONFIG
chown presspeech-agent:presspeech-agent /var/lib/presspeech-agent/.ssh/config
chmod 0600 /var/lib/presspeech-agent/.ssh/config

if [[ ! -d /srv/presspeech/presspeech/.git ]]; then
  sudo -u presspeech-agent -H git clone https://github.com/rcourtman/presspeech.git /srv/presspeech/presspeech
fi
chown -R presspeech-agent:presspeech /srv/presspeech/presspeech /var/lib/presspeech-maintainer
chmod 2775 /srv/presspeech/presspeech
sudo -u presspeech-agent -H git -C /srv/presspeech/presspeech config user.name "Presspeech Maintainer"
sudo -u presspeech-agent -H git -C /srv/presspeech/presspeech config user.email "rcourtman@users.noreply.github.com"

systemctl daemon-reload
systemctl disable --now presspeech-maintainer.timer 2>/dev/null || true
rm -f /etc/presspeech-maintainer/enabled

echo
echo "Installed but not commissioned. Complete these one-time steps:"
echo "  sudo tailscale up --ssh --advertise-tags=tag:infra"
echo "  sudo -u presspeech-agent -H env HOME=/var/lib/presspeech-agent CODEX_HOME=/var/lib/presspeech-agent/codex codex login --device-auth"
echo "  sudo -u presspeech-agent -H env HOME=/var/lib/presspeech-agent gh auth login --hostname github.com --git-protocol https --web"
echo "  sudo -u presspeech-agent -H env HOME=/var/lib/presspeech-agent gh auth setup-git"
echo "Then authorize these dedicated QA keys on their respective hosts:"
echo "Mac Mini:"
cat "${mac_qa_key}.pub"
echo "Windows VM:"
cat "${windows_qa_key}.pub"
REMOTE
