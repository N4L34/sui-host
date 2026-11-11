# Egress acceptance evidence

The local implementation is executable and opt-in. No live deployment, real upstream authentication, host firewall change, client migration or secrets inspection was performed. A generated `enforced` installation is accepted only with a complete private inventory, boot marker and matching actual interfaces; an actual installation is not operationally accepted until its live drills pass.

## Local verification

Verified on 2026-11-12 in a clean copy containing only tracked/task source files: the complete `scripts/ci.sh` passed, including 56 unit tests, five protocol suites, three native deadline suites, the DoH/runtime/stream fixture, two isolated kernel suites, real three-container Compose smoke, Prometheus rule tests, lint and gitleaks. No real operator secrets or live-node configuration were used.

Required command: `bash scripts/test_egress.sh`. It runs unit tests, synthetic private installation/Compose/node-policy checks, builds the pinned test image and executes these fixtures without external networking:

- `singbox_lab.py`: generated HTTP CONNECT + SOCKS5 frontend × HTTPS CONNECT / TLS SOCKS5 upstream; Unicode/newline artificial auth; numeric destination at upstream; origin TLS/auth stripping; HTTP absolute form; IPv4/IPv6 literal destinations; mixed public/private/special/CIDR candidates; unknown target/port/source; bad auth/TLS/stopped upstream; BIND/UDP rejection.
- `deadline_lab.py`: the same patched native runtime bounds blackholed TLS, withheld/slow-drip HTTP CONNECT, incomplete SOCKS authentication/CONNECT, repeated addresses and blackholed DNS. Client EOF and upstream closure prove the native deadlines; the test does not cancel requests using the probe helper's timer.
- `runtime_lab.py`: actual TLS DoH through authenticated upstream, separate gateway/DNS supervisors, status workers, real NXDOMAIN/SERVFAIL, no fallback, primary/backup selection without DNS bootstrap recursion, bad-credential LKG, policy revocation with no remaining app allow, and closure of an active TLS SSE tunnel within 30 seconds.
- `kernel_lab.py`: disposable Linux NET_ADMIN namespace, transactional inet+bridge nft validation/application, real selected-source TCP deny with unaffected traffic, retained tombstones, actual interface discovery and root-owned current-boot guard (wrong boot/IP/MAC/extra interface/writable marker deny). A public test address exists only in that disposable namespace: DIRECT succeeds and an unavailable PROXY sharing its IP cannot use that direct path.

A real disposable Compose smoke additionally runs the three production role containers on dedicated static IP/MAC bridges with separate private mounts and a test-only monitoring network. Authorized HTTP/SOCKS clients pass, a foreign source is denied, staging ports are unreachable from ingress, and role logs are checked for the artificial credential.

The older `gost_lab.py` remains an optional candidate-history test. Its two positive reproductions of unsafe hop-bypass/remote-DNS assumptions explain why those configurations are not used. The current runtime uses resolved numeric targets, fixed GOST chains and no bypass.

## AC-01–18

| AC | Implemented/local evidence | Live evidence still required |
| --- | --- | --- |
| 01 | Real Compose/unit/descriptor, separate role mounts, private ingress/control/uplink, hardened 512 MiB total, no proxy publication, node-policy render | Actual UID/mount ownership, network isolation and discover enrollment |
| 02 | Both frontends and encrypted upstream types, absolute form, end-to-end TLS, domain/IPv4/IPv6 destinations through upstream | Real provider support for numeric CONNECT and actual application SDKs |
| 03 | Registry/compiler/oracle default deny; candidate-set guards; domain/address conflict denial; DIRECT and PROXY same-IP isolation | Approved real route matrix and controlled-origin source evidence |
| 04 | Bad auth/TLS/stopped upstream never reaches origin; independent bootstrap health selects allowed backup; no DIRECT member in selectors; native 15s total/3s DNS/5s handshake blackhole tests | Real outage/drop/reset and provider timing/packet evidence |
| 05 | Scoped output rules persist independently of gateway; startup guard blocks missing policy; selected-source socket denied | Remove env/stop gateway/wrong port from actual client namespaces, internal peer baselines |
| 06 | TCP-only SOCKS5; UDP/BIND deny; fixed IP/MAC/ARP constraints, IPv6 bridge deny and native client IPv6 disable | Raw IPv4/IPv6/UDP/QUIC/host/same-bridge on every actual client interface |
| 07 | Actual DoH relay via upstream; explicit resolver bootstrap; local service records; no system fallback/cache | Docker embedded DNS forwarding, internal names, packet counters on real interfaces |
| 08 | ANY-candidate private/special/explicit deny; mixed DoH response and NXDOMAIN/SERVFAIL tests; numeric upstream target | Real CNAME/rebinding/TTL drill; IPv4-mapped normalization behavior on actual client libraries |
| 09 | File-only TLS-protected auth; special characters; staged changed-auth checks; invalid credential preserves LKG | Real expiration/rotation/revocation and mounted UID/CA verification |
| 10 | Private I/O/digests, safe argparse/native diagnostic discard, bounded error pipes before Docker logs, no auth in DNS/status/Compose/env/argv/metrics | Canary check of actual Docker metadata/Loki/rotation/panic paths in the chosen installation |
| 11 | Per-profile binds/source ACL; GOST source admission; monitoring-only status; private loopback native API; aggregate API source ACL | Foreign container/monitoring/host reachability and spoof attempts |
| 12 | Blind stream relay, bounded native/probe/client handshakes; active TLS SSE tunnel survives >18s and is revoked/cancelled; no payload retry in transport selection | Long polling/WebSocket/HTTP2/partial POST/reset/backpressure/FD duration tests at application load |
| 13 | Pinned/effective path probes, safe primary/backup selection and bounded bootstrap checks; degraded warning rule | Provider outage/recovery and all-failed live drill with origin counters |
| 14 | Bundle integrity + structural invariants + native check; staged auth, LKG, immediate child/tunnel termination; tombstones on apply/rollback; current-boot actual-interface guard | Actual boot/recreate/Docker restart/OOM/marker inventory lifecycle drill |
| 15 | Real scheduler/cached endpoints, hysteresis/freshness, sanitized native aggregates/config rejection, discover labels; missing-series and warning rules | Discover/scrape timing, page delivery after 2 min, independent alert channel, real Loki canary |
| 16 | Optional generated overlays and strict selected client connector/browser modes; defaults unchanged; external images preserved and opt-in local image tags | One client at a time, actual original entrypoint, Matrix/internal health, chat/Grafana IDs, 24-hour observation |
| 17 | FD/pid/CPU/memory bounds and bounded workers/buffers/config input | Agreed peak load, saturation, p95 dial, long-stream FD/memory measurements |
| 18 | Hook stops new failed first-install containers; prior private inventory regenerated on rollback; removed identities remain deny; no global flush/down/direct rollback | Real first-install rollback of compatible image/private files/rules and operator recovery rehearsal |

## Required installation inputs and guardrails

Before applying host policy, verify rootful Linux Docker/Compose versions, br_netfilter availability and both bridge filtering sysctls already set to `1`; nft backend/kernel support; nonoverlapping IPAM; all monitoring/ingress interfaces and stable MAC/IP reservations; actual UID/GID; exact internal peers/DNS; approved destinations/probe target; authenticated encrypted upstream and CA; resource budget across active projects; independent alert delivery.

`enforcement apply` is an explicit operator action requiring a validated private file, root and a private backup path. It never modifies global host DNS/routes/sysctls/Docker firewall policy. It refuses a partial/unowned table state. The root-owned host configuration unit is distinct from the unprivileged application unit; it does not block unrelated Docker services on failure. Client and enforced gateway-role guards fail before launching network processes when their root current-boot marker or actual interfaces differ. Stopping the unit does not remove deny. Tombstones have no automatic cleanup: decommission/reuse needs a separate verified operator procedure.

Keep all real config and rule output private; commands return fixed status codes. Rollback input contains validated inventory, never arbitrary nft text. Backup/active inventory remain local. A missing first-install backup cannot remove deny rules. Guard/marker protects against application-level drift, not a malicious root/Docker-admin.

## Explicit operating limits

Native client IPv6 is disabled; the proxy supports IPv6 destinations through the encrypted upstream. This initial topology does not provide native DIRECT IPv6. It must fail closed rather than silently use a different route. Unsupported upstream protocols/Xray transports are rejected; no existing live Xray file is imported.

The pinned native patch enforces a 15-second establishment budget across address retries, a three-second DNS budget, and a shared five-second TCP/TLS/proxy-handshake budget, preserving any earlier caller deadline. Synthetic blackhole/slow-drip tests verify native socket closure independently of client timeouts; established stream deadlines are cleared. Real provider response behavior, stream idle limits, circuit recovery and peak-load timing still require installation acceptance. FD/pid/memory limits bound retained work.

Native telemetry is aggregate: active PROXY/DIRECT counts and engine byte totals include controlled probes/DNS transport, reset with native restart, and have no per-destination labels. Error counts are native error events, not exact per-route denied-request counts. No raw native API, rule text or destination metrics are exported. Resource/FD saturation also comes from existing cAdvisor; no new dashboard is introduced.

The repository's live node and real credentials remain untouched. Local synthetic tests support review and implementation quality; they do not substitute for this installation's live packet, lifecycle, load and recovery evidence.
