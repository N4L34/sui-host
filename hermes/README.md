# Hermes

The pinned stock image includes Docker CLI. This deployment explicitly grants
Hermes access to the host Docker daemon via `/var/run/docker.sock`; DOCKER_HOST
is pinned to that Unix socket. The stock entrypoint maps HERMES_UID/GID and adds
the application user to the socket GID before dropping privileges. No host socket
chmod is required. Docker API access is effectively host administrator access,
including the ability to create privileged containers and mount host files.
This is an intentional operator capability, not project-scoped access.

Egress proxy settings remain in the locally managed Compose override. Verify
Docker access as the `hermes` user, not only as the container bootstrap root:
`docker compose exec --user hermes hermes docker ps`.
