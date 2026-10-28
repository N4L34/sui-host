# Подключение сервиса

Закон запуска записан в `AGENTS.md`: свой каталог, свой проект Compose, процесс только в контейнере. Этот файл даёт каркас, метки и пример. Если способы запуска расходятся, побеждает `AGENTS.md`.

Новый процесс на узле получает свой каталог в `/home/sui` и свой проект Compose. В проект чата его не добавляют. Сеть `svoi_data` и том Postgres чата ему не отдают. Долгоживущий `docker run` вне Compose не оставляют.

Платформа уже запущена службой `sui-platform.service`. В новый проект её не копируют.

## Каркас контейнера

Образ фиксируют тегом, не `latest`. У долгого сервиса:

- `restart: unless-stopped`;
- `security_opt: [no-new-privileges:true]`;
- журнал `json-file`, `max-size: 10m`, `max-file: "5"`;
- предел памяти;
- `TZ` из окружения, если процессу нужно московское время;
- секрет файлом, не строкой в compose и не в git;
- порт на хост только `127.0.0.1:хост:контейнер`;
- сеть `sui` с `external: true` и `name: sui`;
- `healthcheck`, если есть локальный адрес здоровья.

На `0.0.0.0` и на порт `8448` сервис не сажают. `network_mode: host` не используют. Исключение одно: медиа LiveKit уже слушает адрес Tailscale.

Одноразовая подготовка, которая завершается после работы, получает `restart: "no"`.

## Что подключается само

Память, CPU, перезапуски и журнал появляются сразу: cAdvisor и Alloy читают сокет Docker всего узла. Отдельный дашборд на контейнер не заводят. Общий список уже есть на `https://sui.example.com/`.

Дальше метки. Таймер `sui-discover.timer` раз в 15 секунд читает их и пишет цели Prometheus. Для нового сервиса `prometheus.yml` не правят.

| Метка | Назначение |
| --- | --- |
| `sui.service` | имя серии, латиницей |
| `sui.dns` | имя на сети `sui`, если оно отличается от имени сервиса |
| `sui.probe` | URL HTTP через запятую, например `http://billing:8080/health` |
| `sui.metrics.port` | порт `/metrics` |
| `sui.metrics.path` | путь, если он отличается от `/metrics` |
| `sui.alert` | `page`, если падение проверки должно уйти в Telegram |
| `sui.name` | имя сервиса в тексте алерта |
| `sui.summary` | текст, когда проверка упала |
| `sui.resolved` | текст, когда проверка снова успешна |

Без `sui.alert=page` проверка и метрики есть, page-алерта нет. Текст алерта пишут по-русски и без имён контейнеров.

Метка попадает на контейнер при создании. У уже запущенного контейнера Docker её не меняет. После правки меток контейнер пересоздают. LiveKit при активном звонке не пересоздают.

Если контейнер с `sui.alert=page` удалён, проверка ещё 6 часов остаётся и падает. Затем запись снимается.

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
      - sui
    labels:
      sui.service: billing
      sui.probe: http://billing:8080/health
      sui.metrics.port: "8080"
      sui.metrics.path: /metrics
      sui.alert: page
      sui.name: Оплата
      sui.summary: "Оплата не отвечает."
      sui.resolved: "Оплата снова отвечает."
    deploy:
      resources:
        limits:
          memory: 256M

networks:
  sui:
    external: true
    name: sui
```

Имя `billing` в URL проверки — имя сервиса Compose на сети `sui`. Оно должно резолвиться из контейнера `blackbox`.

Юнит systemd для такого проекта: `Type=oneshot`, `RemainAfterExit=yes`, `ExecStart=docker compose up -d`, `ExecStop=docker compose stop`. В `ExecStop` не ставят `down`: команда удаляет контейнеры.

## Чат

Чат остаётся проектом `svoi`. Изменение самого чата вносят в `tailnet-messenger`. На узле `deploy/compose.platform-net.yml` подключается в `make`, если файл лежит на месте, и присоединяет контейнеры чата к сети `sui`. Во время звонка `make up` не запускают: Compose пересоздаст LiveKit.

`SVOI_OBSERVE` у `svoi.service` равен `false`. Иначе следующий запуск чата поднимет второй Grafana и второй Prometheus на тех же портах.
