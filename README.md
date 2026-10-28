# sui

Платформа узла `sui`: метрики, логи, алерты и контракт подключения сервиса.

Узел `sui.example.com`, адрес `100.64.0.10`. Вход клиентов только через Tailscale Serve. Публичного слушателя нет. Код приложений в этом репозитории не лежит.

| Репозиторий | Содержание |
| --- | --- |
| `N4L34/sui-host` | платформа и правила подключения сервиса |
| `N4L34/tailnet-messenger` | Matrix: Synapse, Element, звонки |
| `N4L34/svoi` | операционные заметки по чату |

## Состав

Проект Compose называется `sui`, каталог на узле `/home/sui/sui-host`, общая сеть Docker тоже называется `sui`.

- `compose.yml` собирает Prometheus, Grafana, Loki, Alloy, Alertmanager, cAdvisor, node-exporter, blackbox-exporter и postgres-exporter.
- `platform/` хранит конфиги и инвентарь целей, у которых ещё нет меток.
- `scripts/discover.py` переносит метки контейнеров в file_sd Prometheus.
- `systemd/` содержит `sui-platform.service` и `sui-discover.timer`.

## Документация

- [Архитектура](docs/architecture.md)
- [Сервисы](docs/services.md)
- [Эксплуатация](docs/work.md)
- [Подключение сервиса](docs/new-service.md)

Перед командами на узле читают `AGENTS.md`.
