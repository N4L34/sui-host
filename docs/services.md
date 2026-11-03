# Сервисы

Каждый долгий сервис располагается в отдельной папке и запускается собственным Compose-проектом. При необходимости проект подключается к общей сети мониторинга. Список deployable проектов автоматически строится из `compose.yml` и связанных systemd-юнитов; нестандартный проект объявляется в `deploy/project.json`.

## Роли

| Проект | Назначение |
| --- | --- |
| `chat` | Matrix Synapse, Postgres, Element Web и опциональные звонки |
| `monitoring` | Метрики, журналы, алерты и обнаружение целей |
| `commands` | Telegram-интерфейс для административных операций Matrix |
| `avtobot` | Автоматизация браузера |
| `screen` | Управление дисплеем устройства |
| `hermes` | Локальный AI gateway |

У каждого проекта отдельные тома и конфигурация. Секреты, runtime-файлы, proxy credentials и данные пользователей задаются на хосте; они не являются частью репозитория. Значения `.env.example` служат только шаблонами.

## Мониторинг

Образы закреплены в `monitoring/.env.example`: Prometheus `prom/prometheus:v3.4.0`, Grafana `grafana/grafana:12.0.2`, Loki `grafana/loki:3.4.2`, Alloy `grafana/alloy:v1.8.1`, Alertmanager `prom/alertmanager:v0.28.0`, node-exporter `prom/node-exporter:v1.9.0`, cAdvisor `ghcr.io/google/cadvisor:v0.60.6`, Postgres exporter `prometheuscommunity/postgres-exporter:v0.17.1` и blackbox exporter `prom/blackbox-exporter:v0.25.0`.

Prometheus обнаруживает контейнеры по меткам `monitoring.*`. Метка `monitoring.probe` добавляет HTTP-проверки, `monitoring.metrics.port` — сбор метрик, `monitoring.alert=page` — уведомление при отказе. Общий мониторинг использует cAdvisor и Alloy для метрик и журналов контейнеров. Доступ к Docker socket и `privileged` нужны только этим компонентам мониторинга.

## Запуск и данные

Каждый сервис имеет свой systemd-юнит, который вызывает `docker compose up -d` при старте системы и останавливает Compose-проект при остановке. Короткие задания запускаются таймерами systemd и завершаются после выполнения.

Состояние приложений хранится во внешних Docker volumes или в локальных каталогах данных. Удаление volume может удалить базы, ключи подписи и журналы; перед очисткой определите владельца данных и проверьте резервные копии.
