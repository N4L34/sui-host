# Shared subscription proxy

One Compose service, `proxy`: Xray 26.10.21 plus a small supervisor and cached
HTTP/SOCKS end-to-end health check. No custom native patches, TUN, host routing,
firewall, static IP/MAC, DNS containers or per-client VPN containers.

- HTTP CONNECT: `http://sui-egress:3128`
- SOCKS5 with remote destination DNS: `socks5h://sui-egress:1080`
- Health inside monitoring: `http://sui-egress:8080/health`
- No published host ports. The existing monitoring network is trusted: its
  members can use the proxy. This is opt-in application routing, not enforcement.

Store the HTTPS subscription URL in `egress/secrets/subscription.url` (0600,
parent directory 0700). It is a credential. Never print it or native config.
`egress/clients.json` lists selected `project/service` pairs. The current parser
accepts base64/plain VLESS TCP + Reality subscriptions and uses the first entry.
UDP is not exposed. Unsupported subscriptions fail without replacing the active
config. Credentials exist only in private native configuration, never client env.

After the initial operator deployment:

```sh
./scripts/egress apply
```

The command downloads the subscription, tests both frontends using a temporary
loopback-only Xray process in the existing container, atomically publishes the
private config, waits for readiness, then updates managed Compose overrides.
Xray restarts inside the same container on a changed validated config. Connections
may reconnect. Unchanged client overrides do not recreate applications.

Each application retains its own Compose project, networks, volumes, image and
entrypoint. The generated `compose.override.yml` contains only local proxy env
and a management marker. An unmanaged pre-existing override is never overwritten.
Commands and avtobot use their explicit SOCKS adapter with no direct-first retry;
Hermes uses HTTP proxy env. Internal Synapse is in NO_PROXY. Unknown applications
must support proxy env or need an application adapter; this is not transparent
traffic interception. Browser tooling must support its configured HTTP proxy.

First installation: render `runtime/config.json` with subscription_config,
build the pinned image, start `docker compose up -d` from this directory and
wait for HTTP 200 at /health before applying clients. CI uses artificial URIs;
real provider and client checks are performed only on the node. Restarts load
the last private config even when the subscription website is unavailable.
A failed proxy never intentionally selects a direct upstream. Applications that
ignore proxy settings can still connect directly; no host enforcement is installed.

The memory limit is 192 MiB for the whole container. Measure live usage with
`docker stats`; this limit is not an expected average.
