#!/usr/bin/env bash
# One-time production setup for the restricted GitHub Actions deploy account.
#
# Usage as root:
#   bash scripts/provision-deploy-user.sh /path/to/deploy-key.pub

set -Eeuo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$(id -u)" -eq 0 ]] || fail "run as root"
[[ $# -eq 1 ]] || fail "usage: $0 /path/to/deploy-key.pub"

readonly PUBLIC_KEY_FILE="$1"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$PUBLIC_KEY_FILE" ]] || fail "public key not found: $PUBLIC_KEY_FILE"

public_key="$(tr -d '\r\n' < "$PUBLIC_KEY_FILE")"
[[ "$public_key" =~ ^ssh-ed25519[[:space:]][A-Za-z0-9+/=]+([[:space:]].*)?$ ]] \
  || fail "expected one ssh-ed25519 public key"

if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --user-group deploy
fi
usermod --shell /bin/bash deploy
passwd --lock deploy >/dev/null 2>&1 || true

install -o root -g root -m 0755 \
  "$SCRIPT_DIR/mailing-agent-deploy-dispatch" \
  /usr/local/bin/mailing-agent-deploy-dispatch
install -o root -g root -m 0755 \
  "$SCRIPT_DIR/mailing-agent-deploy-root" \
  /usr/local/sbin/mailing-agent-deploy-root

sudoers_tmp="$(mktemp)"
authorized_keys_tmp="$(mktemp)"
trap 'rm -f "$sudoers_tmp" "$authorized_keys_tmp"' EXIT

printf '%s\n' \
  'deploy ALL=(root) NOPASSWD: /usr/local/sbin/mailing-agent-deploy-root *' \
  > "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
install -o root -g root -m 0440 \
  "$sudoers_tmp" /etc/sudoers.d/mailing-agent-deploy

install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
printf '%s %s\n' \
  'command="/usr/local/bin/mailing-agent-deploy-dispatch",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding' \
  "$public_key" \
  > "$authorized_keys_tmp"
install -o deploy -g deploy -m 0600 \
  "$authorized_keys_tmp" /home/deploy/.ssh/authorized_keys

sshd -t
sudo -u deploy sudo -n /usr/local/sbin/mailing-agent-deploy-root invalid-sha \
  >/dev/null 2>&1 && fail "restricted sudo validation unexpectedly succeeded"

printf '%s\n' "Restricted deploy user configured successfully."
