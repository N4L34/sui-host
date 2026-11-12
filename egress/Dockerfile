FROM --platform=$BUILDPLATFORM golang:1.27.2-alpine3.24@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d AS singbox
RUN apk add --no-cache patch
ADD --checksum=sha256:9331d29d82a2fdcc432945288a635edc5fdaf39007465165cc133f7f41882087 https://codeload.github.com/SagerNet/sing-box/tar.gz/25a600db24f7680ad9806ce5427bd0ab8afe1114 /tmp/source.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/source.tar.gz --strip-components=1
COPY patches/sing-box-1.13.14-sui.2.patch /patches/native.patch
RUN patch --batch --fuzz=0 -p1 < /patches/native.patch
ENV CGO_ENABLED=0
ARG TARGETARCH
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    GOARCH=$TARGETARCH go build -mod=readonly -trimpath -buildvcs=false -tags with_clash_api,with_utls \
    -ldflags '-s -w -buildid= -X github.com/sagernet/sing-box/constant.Version=1.13.14-sui.2' \
    -o /out/sing-box ./cmd/sing-box
FROM gogost/gost:3.3.3 AS gost
FROM python:3.12.12-alpine3.22 AS runtime
COPY --from=singbox /out/sing-box /usr/local/bin/sing-box
COPY --from=singbox /src/LICENSE /usr/share/licenses/sui-egress/sing-box.LICENSE
COPY --from=singbox /patches/native.patch /usr/share/doc/sui-egress/native.patch
COPY --from=gost /bin/gost /usr/local/bin/gost
COPY patches/NOTICE.md /usr/share/doc/sui-egress/NOTICE.md
WORKDIR /app
COPY *.py /app/egress/
USER 1000:1000
ENTRYPOINT ["python3", "-m", "egress.daemon"]

FROM runtime AS test
USER 0:0
RUN apk add --no-cache openssl nftables iproute2
USER 1000:1000
