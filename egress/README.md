# Platform egress

Runnable opt-in Compose project for HTTP forward/CONNECT and SOCKS5 CONNECT. `sing-box 1.13.14-sui.2` enforces resolved-address policy and supplies verified TLS HTTP/SOCKS5 and VLESS/Reality upstream transports; `GOST 3.3.3` supplies CONNECT-only SOCKS5 frontends without remote credentials. The small pinned native patch and reproducible build are documented in [source provenance](patches/NOTICE.md). Python supervises processes, validates private bundles, tests rotations, selects an allowed backup, and serves cached health/allowlisted telemetry. Client enrollment is an explicit operator operation; a built image does not mean a live migration has succeeded.

There is no host port or user page. Status is `http://sui-egress-status:8080/health/ready` inside `monitoring`; metrics are `/metrics`. Each selected profile has its own internal ingress network. Gateway listeners bind only its configured ingress addresses. DNS/status never receive upstream credentials. No `chat_data`, Docker socket, host networking or application privileges are used.

## Private installation

Copy `examples/installation.example.json` into a host-only `secrets/installation.json`, in a directory owned by the runtime UID with mode `0700`; file mode `0600`. Replace all fictitious destinations, public IPs, IPAM, owner IDs and credentials locally. The example is a schema example, not usable upstream access. Do not print the resulting JSON, authenticated URIs, `.env`, or generated native configs.

The closed schema is executable in `compiler.py` and covered by `tests/test_runtime.py`. Inputs include:

- Public version/route/profile/upstream IDs; a default-deny registry with exact/suffix/CIDR TCP rules and explicit ports.
- UID/GID matching the file owner; nonoverlapping IPv4 ingress/control/uplink subnets; static addresses on **all** interfaces, including existing monitoring.
- `https` (HTTP CONNECT with Basic over verified TLS) or `socks5-tls` (SOCKS5 username/password inside verified TLS), or `vless-reality` (authenticated TCP over REALITY). Numeric upstream bootstrap IP, TLS server name, credentials and optional PEM CA. REALITY uses a closed set of UUID, server public key, hexadecimal short ID, supported uTLS fingerprint and optional Vision flow; arbitrary Xray JSON is never executed. Plain authenticated transports, insecure TLS and unknown fields are rejected.
- Resolver numeric bootstrap IP, HTTPS path/name/CA and an allowed upstream group; a controlled TLS probe target. The probe may declare one exact `expected_status` from 200 through 399 (default 200); redirects are checked without following them. Upstream providers must support the numeric destination sent by the gateway. No remote target re-resolution is accepted.
- Optional exact `internal_dns` records; `internal_peers` per profile (exact IP/protocol/ports and optional exact names); `direct_addresses` mapping DIRECT route IDs to public IPv4 addresses for host enforcement; `monitoring_sources` exact probe/scraper addresses.
- For enforced installations: a validated `enforcement` inventory described below. A bare `mode: enforced` cannot start an accepted deployment.

From the repository root, render without starting anything:

```bash
python3 -m egress.install egress/secrets/installation.json --destination egress
```

The renderer emits only a fixed result. It creates ignored `egress/compose.override.yml` and `runtime/{gateway,dns,status}/runtime.json`, each atomically replaced and private. Native auth exists only in the gateway role directory and its private tmpfs. Directory mounts avoid stale file-bind inodes on replacement. `.dockerignore` whitelists Python source and the reviewed native patches/notices; secrets/runtime never enter the build context or image.

A bundle integrity digest is private, never a public policy version. Runtime validates both the digest and structural invariants before native startup or reload: final reject, candidate guard, controlled DNS, encrypted auth, fixed GOST chains, no DIRECT in selectors, bounded listeners and safe logging. Hand editing generated bundles is unsupported; change the installation and render again.

## Routing and DNS

Gateway resolves every new hostname through controlled DNS, then rejects a connection if **any** candidate is special/private/local/explicitly denied. Exact IP DENY applies to DNS candidates too. Domain/address route collisions reject; CIDR-only allows use complement guards so every candidate must have the same action/group. Domain rules remain distinct even when PROXY and DIRECT share an IP. Unknown routes and UDP reject; no automatic DIRECT fallback exists.

Validated numeric addresses are passed to the encrypted upstream. DIRECT goes from the gateway, not through client `NO_PROXY`. HTTP absolute form/CONNECT, SOCKS domain/IPv4/IPv6 destinations and origin TLS are tested. Native client IPv6 is deliberately disabled by the enforced overlay and bridge rules; IPv6 destinations are supported through the upstream's TCP CONNECT transport. This initial topology has no native IPv6 uplink for DIRECT: a DIRECT IPv6 route fails closed. An installation needing native DIRECT IPv6 must supply and validate a dual-stack host-policy topology before enabling it.

DNS is a separate sing-box process, source-restricted on configured ingress/control IPs. It sends verified HTTPS DNS through a credential-free gateway service listener. The DoH bootstrap IP is explicit, so DNS cannot depend on resolving itself. No public/system fallback or stale cache is configured; caching is disabled, so negative results cannot outlive their TTL. Docker service names resolve locally; explicitly needed additional internal names/addresses can be supplied as private records. Actual Docker embedded DNS forwarding and browser leakage still require the live packet tests in `acceptance.md`.

GOST is used only with fixed chains. Its hop bypass is not a policy or failover mechanism. SOCKS4, BIND and UDP ASSOCIATE are rejected by the SOCKS5 handler. sing-box's native SOCKS inbound enables additional protocols, so it is intentionally not exposed.

## Reload, rotation and failures

The container supervisor polls its role file every second. A candidate passes closed-schema/integrity checks and the pinned native parser before activation. Gateway stages it on alternate ports. Changed upstream credentials/transports must pass both pinned frontend probes twice before activation; policy-only revocations do not depend on a failed backup. Invalid candidates preserve the in-memory LKG and emit a fixed `config_rejected` once per changed input file. A valid replacement terminates old native processes and tunnels immediately (two-second termination bound), including removal of the last allowed destination. Cold invalid configuration starts no proxy listeners.

A primary/backup selector contains only configured proxy nodes. Independent numeric bootstrap/TLS probes avoid a DNS/failover cycle: after three failed checks an unavailable primary is replaced by a healthy backup; two successes allow recovery. Background checks run every five seconds; individual checks are bounded. Connections are never retried after payload transmission. On both failures the selected proxy fails; DIRECT is never selected. Stream traffic is relayed without a 30-second total request timeout. FD/pid/memory/CPU limits bound saturation; live p95/concurrency acceptance is separate.

The patched native runtime bounds connection establishment independently of the application's SDK: 15 seconds across resolution/address retries, 3 seconds for resolution, and 5 shared seconds for TCP/TLS/proxy authentication and CONNECT. An earlier caller deadline wins. Actual socket deadlines cover blocking protocol reads and are cleared on successful establishment. Synthetic blackhole/slow-drip tests use client EOF and upstream closure as their oracle; an established stream survives more than 18 seconds and remains revocable. These are establishment limits, not a total lifetime limit for working streams.

A failed credential update must not be fixed with anonymous/insecure/direct access. Restore a compatible local private installation/image and render it, or retain deny and stop only the affected client. Revoked credentials are not restored from Git.

## Scoped host enforcement and boot lifecycle

`enforcement.py` implements validation, private render, kernel check, atomic apply, rollback and Docker identity verification. The only supported backend is nftables with existing `bridge-nf-call-iptables=1` and `bridge-nf-call-ip6tables=1`. Apply verifies these; it does not change sysctls, host DNS/routes, Docker management, global policies or other tables.

The private inventory names each client/gateway/DNS/status identity, role/project/service/profile, **every** network IP/MAC pair, exact outgoing endpoints and permitted incoming peers/ports. Locally administered unicast MACs and IPv4 addresses must be unique/reserved. Enforced client outgoing endpoints are restricted to its gateway, controlled DNS and declared internal peers. Gateway/DNS/status also have scoped output allowlists; dynamic DIRECT domains need explicit public address sets. The status identity must explicitly allow its own monitoring address on TCP 8080 in both outgoing and incoming inventories for its post-up self-check. The gateway identity similarly allows its own control address on TCP 23000 + upstream index for independent pinned upstream probes. The compiler assigns MAC/IP settings to all gateway role networks. Client overlays do the same.

Own `inet sui_egress` and `bridge sui_egress` tables have accept defaults outside selected sources. They cover routed, host and same-bridge traffic, identity/ARP spoofing, native IPv6 and exact tuple replies. Original outgoing traffic has no blanket ESTABLISHED exemption. Bridge rules work with the required inet bridge filtering, so kernels need not implement bridge conntrack. Rules persist across Docker daemon restarts. Deleted identities remain deny-only tombstones on apply **and rollback**; there is no automatic decommission that can open an old address.

Install the example `examples/sui-egress-policy.service` separately as a bounded host configuration task, with root-owned `0700` `/etc/sui-egress` and `0600` inventory. It runs before Docker when possible; failure does not prevent unrelated Docker projects starting. Apply writes a root-owned marker in ephemeral `/run/sui-egress-policy`, tied to this kernel boot and each validated identity. Only opted-in containers mount it read-only. Their startup guard compares **actual Linux IP/MAC assignments and every active/nonempty interface**, verifies native IPv6 is absent, then execs the supplied original entrypoint/command. A missing/stale marker, added interface or changed IP/MAC prevents application startup. A recreated client cannot race an absent firewall during boot. Removing/stopping the host unit does not remove deny rules.

Operator-only commands, after reviewing actual inventory and host requirements:

```bash
python3 -m egress.enforcement render /etc/sui-egress/inventory.json --output /etc/sui-egress/rendered.json
python3 -m egress.enforcement check /etc/sui-egress/inventory.json
sudo python3 -m egress.enforcement apply /etc/sui-egress/inventory.json --backup /etc/sui-egress/previous.json
sudo python3 -m egress.enforcement rollback /etc/sui-egress/previous.json
```

Render writes a private file, not endpoint rules to stdout. Rollback recompiles a validated prior inventory; it never executes arbitrary saved nft text or removes the first-install deny. Active inventory and backup must remain compatible with the checked-out code. Source/MAC reservations and the root marker directory are operator-owned; Docker-admin/root are trusted. Do not reuse retired identities. Complete the boot/recreate/Docker-restart/packet drill before calling a live profile enforced.

## Opt-in clients

Current Compose files and default legacy routing remain unchanged. `migrate.client_overlay(config, identity_id, original_entrypoint_and_command)` builds a selected-client override. Call it only with a compiled installation and the **verified original image entrypoint plus command**. Persist with `write_private_json`; Compose accepts JSON YAML. Do not guess Hermes' image entrypoint. The default renderer preserves external images; the Hermes non-root derivative described below is an explicit installation override; commands/avtobot receive new `2026.11.10-egress` image tags and must be explicitly built before their opt-in rollout.

The overlay supplies upper/lowercase proxy variables, clears ALL_PROXY, generates NO_PROXY solely from localhost/self/declared internal peers, adds per-container DNS, static identity, cap-drop, IPv6 disable and startup guard. Existing monitoring membership must be present in the private inventory as a static interface. Newly added ingress networks are external and pre-created by egress.

`commands` and `avtobot` select `SUI_EGRESS_MODE=required` to use a credential-free, domain-preserving SOCKS connector with one total deadline. Required mode never enters direct-first or Xray startup. Unknown modes/credential URIs fail. Legacy mode is the default. The opt-in avtobot Chrome arguments set the HTTP proxy and disable QUIC/DoH/system-resolver fallback; nft remains the bypass boundary. Sidecars are disabled by the generated profile overlay, not removed from active Compose. Validate Matrix/internal peer flows before rollout and observe each client for 24 hours before deleting any old transport.

## Deploy, monitoring and rollback

`systemd/egress.service` only runs Compose up/stop. The service catalog discovers the real project and `deploy/project.json` selects `deploy.sh`. The hook validates private input before up, builds only egress, then runs `smoke.py` inside status with a 60-second deadline. Both required effective frontends, controlled DNS and cached fresh readiness must succeed twice. Every pinned upstream is checked; an optional failed primary remains a visible warning when the effective backup works. `up -d` or a TCP listener is insufficient. First-install smoke failure stops only newly created egress containers; existing deployments retain host deny and require private/image rollback. Never use `down`, global flush, chat/monitoring restarts or a direct-first client rollback.

Before a live launch, follow the four lines in repository `AGENTS.md`, inspect `docker compose ps`, cached health, discover targets and unaffected chat/Grafana IDs. No host port exists. Record actual launch and migration outcomes separately from synthetic verification.

Status uses separate bounded workers, a 15-second jittered schedule, 15-second deadlines and cached endpoints. Two successes recover, three failures degrade, 60-second freshness expires. Liveness remains independent of upstream failure. Each pinned path is visible when an effective backup is healthy. Existing blackbox page alerts handle ready failure after two minutes; added rules cover missing status series for enrolled targets and degraded upstream warnings. An independent alert delivery channel remains a live installation input.

Only a private loopback Clash API is enabled. Gateway exports numeric allowlisted aggregates to status over control networking: active connections, current active PROXY/DIRECT counts, native byte totals, native error count, config rejection count and last config-apply time. Native stderr is drained in bounded private pipes and reduced to counters **before Docker logs**; raw data is never persisted/emitted. Native byte counters reset with native restart, include probes/DNS transport and do not represent application billing. Error counts reflect native error log events, not exact per-route denied-request totals. Metrics contain no destination, URI, auth, arbitrary rule text, connection ID or private integrity hash. cAdvisor supplies resource/FD saturation context; no new dashboard or monitoring stack is created.

## Verification and sources

`bash scripts/test_egress.sh` is required in CI and does not skip Docker tests. It copies only explicit public source fixtures into disposable containers. It checks unit/schema/private-file contracts, synthetic Compose/node policy, both pinned binaries, controlled-DoH runtime/failover/LKG/revocation/stream behavior and nft/guard/DIRECT isolation in a NET_ADMIN **test container** with no network. Production containers drop ALL capabilities. See `acceptance.md` for exact local evidence and outstanding live acceptance.

Pinned behavior was checked against official source, including [resolve/routing](https://github.com/SagerNet/sing-box/blob/v1.13.14/route/route.go), [ANY-candidate CIDR matching](https://github.com/SagerNet/sing-box/blob/v1.13.14/route/rule/rule_item_cidr.go), [private-address matching](https://github.com/SagerNet/sing-box/blob/v1.13.14/route/rule/rule_item_ip_is_private.go), [HTTP TLS outbound](https://github.com/SagerNet/sing-box/blob/v1.13.14/protocol/http/outbound.go), [SOCKS inbound behavior](https://github.com/SagerNet/sing-box/blob/v1.13.14/protocol/socks/inbound.go), [native traffic counters](https://github.com/SagerNet/sing-box/blob/v1.13.14/experimental/clashapi/trafficontrol/manager.go), [GOST admission](https://gost.run/en/concepts/admission/) and [GOST bypass semantics](https://gost.run/en/concepts/bypass/). These source checks accompany executable fixtures; they do not replace the live packet/lifecycle tests.

For Hermes v0.22.5, `hermes/Dockerfile.egress` remaps the image user to UID/GID 1000 at build time and prepares its original bootstrap environment directory. Use the resulting `sui-hermes-egress:v0.22.5-2026.11.12` image with `user: 1000:1000` in the private overlay and retain the original entrypoint/command. This permits cap-drop ALL without root startup or SETUID/CHOWN capabilities; data-volume ownership must already match. Build/test it before opting in; the base Hermes Compose remains unchanged.
