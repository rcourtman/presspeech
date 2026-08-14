# Presspeech autonomous maintainer

This directory defines the small, dedicated `presspeech-dev` VM on the
`pve-pc` Proxmox node.

The VM deliberately has a much smaller operating model than Pulse Dev:

- Ubuntu 24.04 LTS, four visible vCPUs with a three-core host cap, 8 GiB RAM
  ballooning to 4 GiB, a 32 GiB thin disk, and 4 GiB emergency swap.
- One unattended identity, one repository, one systemd timer, and one Codex
  invocation per day. The timer opens its window at 07:15 Europe/London and
  adds up to 45 minutes of random delay to avoid relying on an exact minute.
- The model receives one short stewardship prompt. Durable project policy stays
  in the repository's `AGENTS.md`; recent work stays in Git and the local run
  archive.
- Native verification is delegated to the Mac Mini by `presspeech-mac-qa` and
  to the Windows VM by `presspeech-windows-qa`.
- The maintainer has delegated authority to decide when accumulated, verified
  work warrants a release. macOS signing and notarisation run through a queued
  LaunchAgent in the logged-in Mac session; Windows releases use the guarded
  GitHub Actions workflow. Credentials remain on their native hosts.

## Provision

From a trusted Mac with `ssh pve-pc` access:

```sh
infra/maintainer-vm/provision-vm.sh
```

The provisioner refuses to overwrite an existing VM 261. It downloads the
official Ubuntu Noble cloud image, verifies it against the published SHA-256
manifest, creates the VM, and starts cloud-init. Wait for cloud-init to finish
before installing the maintainer runtime.

Then install the maintainer runtime from this checkout:

```sh
infra/maintainer-vm/install-maintainer.sh presspeech-dev.local
```

Account-bound setup is intentionally separate. Authenticate Tailscale,
unattended Codex, and GitHub as documented by the install script's final
readiness report. Do not copy the Pulse maintainer's ChatGPT refresh tokens;
each unattended machine must have its own login state.

The installer creates separate Mac and Windows QA keys and prints their public
halves. Authorize each key only on its named worker. The QA helpers copy a clean
working tree to a disposable directory, run the platform's native tests, and
remove the directory afterwards. After authorizing the Windows key, prepare its
reusable Python 3.12 environment once with:

```sh
ssh presspeech-dev.local sudo -u presspeech-agent -H \
  bootstrap-windows-qa /srv/presspeech/presspeech
```

The installer also registers the Mac release worker in the logged-in GUI
session. Validate its signing, notarisation, GitHub, repository, and Homebrew
access without publishing anything:

```sh
ssh presspeech-dev.local sudo -u presspeech-agent -H presspeech-mac-release doctor
```

Release entry points available to the maintainer are:

```sh
presspeech-mac-release patch
presspeech-mac-release minor
presspeech-mac-release major
presspeech-windows-release X.Y.Z
```

Neither helper exposes a QA bypass. Both refuse dirty or divergent `main`
checkouts; the Windows helper additionally requires native CUDA/ASR QA and
committed version-specific release notes. Mac requests are placed in a
LaunchAgent-watched queue and handled serially, so a later request cannot
terminate signing or notarisation already in progress.

The timer remains disabled until all readiness checks pass. Commission it with:

```sh
ssh presspeech-dev.local sudo presspeech-maintainer-commission activate
```

The daily model prompt is intentionally only:

> Maintain Presspeech to the best of your ability. Choose and complete the most
> valuable honest improvement you can justify today; review recent history to
> avoid repetitive work, and do nothing if no change is warranted.

`AGENTS.md`, Git history, tests, and the local run archive provide the durable
context. Each run is capped at 18 hours so a stuck process cannot block the next
day indefinitely.

Useful commands:

```sh
ssh presspeech-dev.local sudo presspeech-maintainer-commission status
ssh presspeech-dev.local sudo presspeech-maintainer-commission doctor
ssh presspeech-dev.local sudo presspeech-maintainer-commission run-now
ssh presspeech-dev.local sudo presspeech-maintainer-commission pause
ssh presspeech-dev.local sudo journalctl -u presspeech-maintainer.service -n 200
```
