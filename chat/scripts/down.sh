#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"
need_cmd docker
# All profiles, so a later `make down` also stops Grafana and LiveKit.
compose_down_all
