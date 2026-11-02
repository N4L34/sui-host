# sui

Репозиторий узла `sui`. Каждый сервис лежит в своей папке и собирается своим Compose.

Узел `sui.example.com`, адрес Tailscale `100.64.0.10`. Вход клиентов только через Tailscale Serve по этому имени. Публичного слушателя нет. На узле один клон `/home/sui/sui-host`. Список проектов для CI и деплоя строится автоматически через `scripts/service_catalog.py`.

| Папка | Compose | Что делает |
| --- | --- | --- |
| `monitoring` | `monitoring` | Prometheus, Grafana, Loki, алерты |
| `commands` | `commands` | команды чата в Telegram |
| `avtobot` | `avtobot` | номера |
| `screen` | `screen` | панель Sui Play выключена |
| `chat` | `chat` | Matrix из `/home/sui/sui-host/chat` |

`N4L34/svoi` остаётся заметками и на узле не нужен.

- `monitoring/platform/` хранит конфиги мониторинга и инвентарь целей, у которых ещё нет меток.
- `scripts/discover.py` переносит метки контейнеров в file_sd Prometheus.
- `systemd/` содержит юниты сервисов. Юнит вызывает только `docker compose up -d` и `docker compose stop`.

Образы мониторинга закреплены тегами в `monitoring/.env.example`.

## Куда смотреть

Человеку: [архитектура](docs/architecture.md), [сервисы](docs/services.md), [эксплуатация](docs/work.md), [подключение сервиса](docs/new-service.md).

Агенту перед любой командой на узле: `AGENTS.md`. Проверки и выкладка: [эталон](docs/standard.md), команда `scripts/ci.sh`.
