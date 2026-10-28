# sui

Платформа узла `sui`: метрики, логи, алерты и правила, по которым к ней подключают сервис.

Узел `sui.example.com`, адрес Tailscale `100.64.0.10`. Вход клиентов только через Tailscale Serve по этому имени. Публичного слушателя нет. Код приложений в этот репозиторий не кладут: новый проект получает свой каталог и свой Compose.

| Репозиторий | Содержание |
| --- | --- |
| `N4L34/sui-host` | платформа и правила подключения сервиса |
| `N4L34/tailnet-messenger` | Matrix: Synapse, Element, звонки |
| `N4L34/svoi` | операционные заметки по чату |

## Состав

Каталог на узле `/opt/monitoring`. Путь `/home/sui/sui-host` ведёт туда ссылкой. Проект Compose и общая сеть Docker называются `monitoring`. Чат — отдельный проект `chat` в `/home/sui/tailnet-messenger`.

- `compose.yml` собирает Prometheus, Grafana, Loki, Alloy, Alertmanager, cAdvisor, node-exporter, blackbox-exporter и postgres-exporter.
- `platform/` хранит конфиги и инвентарь целей, у которых ещё нет меток.
- `scripts/discover.py` переносит метки контейнеров в file_sd Prometheus.
- `systemd/` содержит `monitoring.service` и `monitoring-discover.timer`.

Образы платформы закреплены тегами в `.env.example`.

## Куда смотреть

Человеку: [архитектура](docs/architecture.md), [сервисы](docs/services.md), [эксплуатация](docs/work.md), [подключение сервиса](docs/new-service.md).

Агенту перед любой командой на узле: `AGENTS.md`. Проверки и выкладка: [эталон](docs/standard.md), команда `scripts/ci.sh`.
