#!/usr/bin/env bash
set -euo pipefail

PVE_HOST="${PVE_HOST:-pve-pc}"
VM_ID="${VM_ID:-261}"
VM_NAME="${VM_NAME:-presspeech-dev}"
STORAGE="${STORAGE:-pve-pc-ssd}"
SNIPPET_STORAGE="${SNIPPET_STORAGE:-pve-pc-snippets}"
IMAGE_URL="${IMAGE_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}"
IMAGE_PATH="${IMAGE_PATH:-/var/lib/vz/template/iso/noble-server-cloudimg-amd64.img}"
DISK_SIZE="${DISK_SIZE:-32G}"
SSH_PUBLIC_KEY="${SSH_PUBLIC_KEY:-${HOME}/.ssh/pve_pc_root.pub}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
user_data="${repo_root}/infra/maintainer-vm/cloud-init/user-data.yaml"

test -r "${user_data}"
test -r "${SSH_PUBLIC_KEY}"

rendered_user_data="$(mktemp)"
trap 'rm -f "${rendered_user_data}"' EXIT
public_key="$(<"${SSH_PUBLIC_KEY}")"
if [[ ! "${public_key}" =~ ^ssh-(ed25519|rsa)[[:space:]] ]]; then
  echo "Unsupported SSH public key: ${SSH_PUBLIC_KEY}" >&2
  exit 2
fi
sed "s|__SSH_PUBLIC_KEY__|${public_key}|" "${user_data}" >"${rendered_user_data}"

# VM_ID is intentionally expanded by this trusted local provisioner.
# shellcheck disable=SC2029
if ssh "${PVE_HOST}" "qm status '${VM_ID}' >/dev/null 2>&1"; then
  echo "VM ${VM_ID} already exists; refusing to overwrite it." >&2
  exit 2
fi

scp "${rendered_user_data}" "${PVE_HOST}:/var/lib/vz/snippets/presspeech-dev-user-data.yaml"

ssh "${PVE_HOST}" bash -s -- \
  "${VM_ID}" "${VM_NAME}" "${STORAGE}" "${SNIPPET_STORAGE}" \
  "${IMAGE_URL}" "${IMAGE_PATH}" "${DISK_SIZE}" <<'REMOTE'
set -euo pipefail
vm_id="$1"
vm_name="$2"
storage="$3"
snippet_storage="$4"
image_url="$5"
image_path="$6"
disk_size="$7"

manifest_url="${image_url%/*}/SHA256SUMS"
mkdir -p "$(dirname "${image_path}")"
tmp_image="${image_path}.download"
tmp_manifest="${image_path}.SHA256SUMS"
curl --fail --location --retry 3 --output "${tmp_image}" "${image_url}"
curl --fail --location --retry 3 --output "${tmp_manifest}" "${manifest_url}"
expected="$(awk -v name="$(basename "${image_url}")" '$2 == name || $2 == "*" name {print $1; exit}' "${tmp_manifest}")"
test -n "${expected}"
actual="$(sha256sum "${tmp_image}" | awk '{print $1}')"
test "${actual}" = "${expected}"
mv -f "${tmp_image}" "${image_path}"
rm -f "${tmp_manifest}"

qm create "${vm_id}" \
  --name "${vm_name}" \
  --description "Dedicated Presspeech autonomous maintenance host" \
  --ostype l26 \
  --machine q35 \
  --cpu host \
  --sockets 1 \
  --cores 4 \
  --cpulimit 3 \
  --cpuunits 512 \
  --memory 8192 \
  --balloon 4096 \
  --scsihw virtio-scsi-single \
  --net0 virtio,bridge=vmbr0,firewall=1 \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --serial0 socket \
  --vga serial0 \
  --tablet 0 \
  --onboot 1 \
  --startup order=5,up=30 \
  --tags "automation;development;presspeech;tailscale"

qm importdisk "${vm_id}" "${image_path}" "${storage}"
qm set "${vm_id}" --scsi0 "${storage}:vm-${vm_id}-disk-0,discard=on,iothread=1,ssd=1"
qm resize "${vm_id}" scsi0 "${disk_size}"
qm set "${vm_id}" --ide2 "${storage}:cloudinit"
qm set "${vm_id}" --boot order=scsi0
qm set "${vm_id}" --ciuser rcourtman
qm set "${vm_id}" --ipconfig0 ip=dhcp
qm set "${vm_id}" --nameserver 192.168.0.1 --searchdomain local
qm set "${vm_id}" --cicustom "user=${snippet_storage}:snippets/presspeech-dev-user-data.yaml"
qm cloudinit update "${vm_id}"
qm start "${vm_id}"
qm config "${vm_id}"
REMOTE

echo "VM ${VM_ID} (${VM_NAME}) created. Wait for cloud-init, then run install-maintainer.sh."
