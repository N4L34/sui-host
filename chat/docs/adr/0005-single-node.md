# ADR 0005. Один узел, Compose, без Kubernetes

## Статус

Принято.

## Контекст

Группа маленькая. Второй узел, общий диск и дежурство по кластеру съедят больше времени, чем сэкономят минуты простоя. Хост — один VPS или один Mac mini, который не спит.

## Решение

Единица выкладки — Docker Compose, проект `svoi`. Три файла:

- `deploy/compose.yml` — Postgres, Synapse, Element Web;
- `deploy/compose.calls.yml` — LiveKit, JWT, Caddy, только при `ENABLE_CALLS=true`;
- `deploy/compose.observe.yml` — Prometheus, Alertmanager, Loki, Alloy, Grafana, только через `make observe`.

`make down` всегда передаёт все три файла, чтобы Grafana и LiveKit не остались после обычной остановки.

Машину вы заводите сами. Репозиторий не создаёт VPS и не пишет правила файрвола. На хосте нужны Docker и Tailscale. Дальше `make up` и `make serve`.

Цель по простою честная: 99.5% в месяц на API клиентов одного узла. Это около 3,6 часа. Автоматического переключения на другую площадку нет.

## Последствия

Диск, гипервизор и питание этой машины — это простой чата. Бэкап — почасовой снимок в `backups/`. Смотрите `docs/sre/backup.md`.
