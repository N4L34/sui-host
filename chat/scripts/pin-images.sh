#!/usr/bin/env bash
# Print image digests after they have been pulled. Paste a digest into .env when you want a hard pin.
source "$(dirname "$0")/lib.sh"
need_cmd docker
load_env

images=(
  "${SYNAPSE_IMAGE}"
  "${POSTGRES_IMAGE}"
  "${ELEMENT_WEB_IMAGE}"
)
if [[ "${ENABLE_CALLS:-false}" == "true" ]]; then
  images+=("${LIVEKIT_IMAGE}" "${LK_JWT_IMAGE}" "${CADDY_IMAGE}")
fi

mkdir -p deploy/rendered
out="deploy/rendered/image-digests.txt"
: > "$out"
for image in "${images[@]}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "pulling ${image}"
    docker pull "$image"
  fi
  digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$image" 2>/dev/null || true)"
  printf '%s\t%s\n' "$image" "${digest:-unpulled}" | tee -a "$out"
done
echo "wrote ${out}"
