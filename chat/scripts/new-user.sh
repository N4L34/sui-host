#!/usr/bin/env bash
# Create a local account. Registration on the public client API stays closed.
# Usage: scripts/new-user.sh alice
#        AS_ADMIN=true scripts/new-user.sh operator
source "$(dirname "$0")/lib.sh"
need_cmd python3 docker
load_env

user="${1:-}"
if [[ ! "$user" =~ ^[a-z0-9._=/-]+$ ]]; then
  echo "usage: make user USER=alice" >&2
  exit 1
fi

pass="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
args=(-c /data/homeserver.yaml -u "$user" -p "$pass")
if [[ "${AS_ADMIN:-false}" == "true" ]]; then
  args+=(-a)
fi

compose exec -T synapse register_new_matrix_user "${args[@]}"
echo
echo "user:     @${user}:${MATRIX_SERVER_NAME}"
echo "password: ${pass}"
echo "Tell them in person or in a channel that is not this chat. They should change it after login."
