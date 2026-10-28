# Подключение сервиса

Закон запуска записан в `AGENTS.md`: свой каталог, свой проект Compose, процесс только в контейнере. Этот файл даёт каркас, метки и пример. Если способы запуска расходятся, побеждает `AGENTS.md`.

Новый процесс на узле получает свой каталог в `/home/sui` и свой проект Compose. В проект чата его не добавляют. Сеть `chat_data` и том Postgres чата ему не отдают. Долгоживущий `docker run` вне Compose не оставляют.

Мониторинг уже запущен службой `monitoring.service` из `/opt/monitoring`. В новый проект его не копируют.

## Каркас контейнера

Образ фиксируют тегом, не `latest`. У долгого сервиса:

- `restart: unless-stopped`;
- `security_opt: [no-new-privileges:true]`;
- журнал `json-file`, `max-size: 10m`, `max-file: "5"`;
- предел памяти;
- `TZ` из окружения, если процессу нужно московское время;
- секрет файлом, не строкой в compose и не в git;
- порт на хост только `127.0.0.1:хост:контейнер`;
- сеть `monitoring` с `external: true` и `name: monitoring`;
- `healthcheck`, если есть локальный адрес здоровья.

На `0.0.0.0` и на порт `8448` сервис не сажают. `network_mode: host` не используют. Исключение одно: медиа LiveKit уже слушает адрес Tailscale.

Одноразовая подготовка, которая завершается после работы, получает `restart: "no"`.

## Что подключается само

Память, CPU, перезапуски и журнал появляются сразу: cAdvisor и Alloy читают сокет Docker всего узла. Отдельный дашборд на контейнер не заводят. Общий список уже есть на `https://sui.example.com/`.

Дальше метки. Таймер `monitoring-discover.timer` раз в 15 секунд читает их и пишет цели Prometheus. Для нового сервиса `prometheus.yml` не правят.

| Метка | Назначение |
| --- | --- |
| `monitoring.service` | имя серии, латиницей |
| `monitoring.dns` | имя на сети `monitoring`, если оно отличается от имени сервиса |
| `monitoring.probe` | URL HTTP через запятую, например `http://billing:8080/health` |
| `monitoring.metrics.port` | порт `/metrics` |
| `monitoring.metrics.path` | путь, если он отличается от `/metrics` |
| `monitoring.alert` | `page`, если падение проверки должно уйти в Telegram |
| `monitoring.name` | имя сервиса в тексте алерта |
| `monitoring.summary` | текст, когда проверка упала |
| `monitoring.resolved` | текст, когда проверка снова успешна |

Без `monitoring.alert=page` проверка и метрики есть, page-алерта нет. Текст алерта пишут по-русски и без имён контейнеров.

Метка попадает на контейнер при создании. У уже запущенного контейнера Docker её не меняет. После правки меток контейнер пересоздают. LiveKit при активном звонке не пересоздают.

Если контейнер с `monitoring.alert=page` удалён, проверка ещё 6 часов остаётся и падает. Затем запись снимается.

Пока у контейнеров чата нет меток, их цели записаны в `platform/services.json`. Метка на контейнере перекрывает эту запись.

## Пример

```yaml
services:
  billing:
    image: ghcr.io/example/billing:1.4.2
    restart: unless-stopped
    security_opt: [no-new-privileges:true]
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    ports:
      - "127.0.0.1:8090:8080"
    networks:
      - monitoring
    labels:
      monitoring.service: billing
      monitoring.probe: http://billing:8080/health
      monitoring.metrics.port: "8080"
      monitoring.metrics.path: /metrics
      monitoring.alert: page
      monitoring.name: Оплата
      monitoring.summary: "Оплата не отвечает."
      monitoring.resolved: "Оплата снова отвечает."
    deploy:
      resources:
        limits:
          memory: 256M

networks:
  monitoring:
    external: true
    name: monitoring
```

Имя `billing` в URL проверки — имя сервиса Compose на сети `monitoring`. Оно должно резолвиться из контейнера `blackbox`.

Юнит systemd для такого проекта: `Type=oneshot`, `RemainAfterExit=yes`, `ExecStart=docker compose up -d`, `ExecStop=docker compose stop`. В `ExecStop` не ставят `down`: команда удаляет контейнеры.

## Чат

Чат лежит в `/home/sui/tailnet-messenger`. Проект Compose называется `chat`: это задаёт `deploy/compose.platform-net.yml`, и `make` подключает файл, если он лежит на месте. Служба systemd по-прежнему `svoi.service`. Во время звонка `make up` не запускают: он вызывает `render_config.py` и пересоздаёт LiveKit.

`SVOI_OBSERVE` у `svoi.service` равен `false`. Иначе следующий запуск чата поднимет второй Grafana и второй Prometheus на тех же портах.
