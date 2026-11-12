# Native source provenance

Runtime and tests use `sing-box 1.13.14-sui.2`, not the unmodified upstream binary.

- Upstream: https://github.com/SagerNet/sing-box
- Release: v1.13.14, source commit `25a600db24f7680ad9806ce5427bd0ab8afe1114`.
- Source archive SHA-256: `9331d29d82a2fdcc432945288a635edc5fdaf39007465165cc133f7f41882087`.
- Dependency lock: original upstream `go.mod`/`go.sum`, including `github.com/sagernet/sing v0.8.11`; `go build -mod=readonly`, normal Go sum verification enabled.
- Build: Go 1.27.2 Alpine 3.24 image pinned by digest in Dockerfile, CGO disabled, trimpath/buildvcs=false, explicit version/build tags, no embedded VCS/build ID.
- License: upstream GPL-3.0-or-later, original notice/LICENSE included in runtime image. Corresponding upstream source is the exact immutable commit above; all local changes are distributed in `sing-box-1.13.14-sui.2.patch` and in the runtime image under `/usr/share/doc/sui-egress/native.patch`.

The local patch adds one total 15-second request establishment context, a three-second DNS resolve budget, and five-second TCP/TLS/proxy handshake deadlines. It uses a real socket deadline for blocking HTTP/SOCKS authentication and CONNECT reads, preserves earlier context deadlines, then clears the handshake socket deadline on success. Successful stream connections are not owned by the dial timeout context. The existing sing-box TLS dialer is reused for SOCKS5 outbound rather than introducing a new protocol engine.

GOST 3.3.3 remains the unmodified upstream CONNECT-only SOCKS frontend. It receives no remote credentials. Upstream project/source/license: https://github.com/go-gost/gost/tree/v3.3.3 . Python/Alpine and their notices remain supplied by the respective base image distributions.

The sui.2 extension bounds native VLESS TCP/REALITY establishment to five seconds and enables the pinned source's uTLS implementation. VLESS payload framing remains upstream code; destination TLS/probe deadlines also cover its lazy response. A synthetic REALITY server tests both frontends, numeric origin routing and rejection of a wrong short ID without external networking.
