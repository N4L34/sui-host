# Новый сервис

Новый процесс на приставке — свой каталог в `/home/sui` и свой проект Compose. В чат его не кладут. Сеть `svoi_data` и том Postgres чата ему не отдают. Отдельный `docker run`, который переживёт перезагрузку только случайно, не оставляют.

Платформа уже запущена службой `sui-platform.service`. Её не копируют внутрь нового проекта.

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

На `0.0.0.0` и на порт `8448` новый сервис не сажают. `network_mode: host` не используют. Исключение по порту одно и уже занято: медиа LiveKit на адресе Tailscale.

Одноразовая подготовка, которая делает дело и завершается, получает `restart: "no"`.

## Что подключается само

Память, CPU, перезапуски и журнал появляются сразу: cAdvisor и Alloy читают сокет Docker со всей машины. Отдельную панель на контейнер не рисуют. Общий список уже есть на `https://sui.example.com/`.

Дальше метки. Таймер `sui-discover.timer` раз в 15 секунд читает их и пишет цели Prometheus. Конфиг Prometheus для нового сервиса не правят.

| Метка | Зачем |
| --- | --- |
| `sui.service` | короткое имя серии, латиницей |
| `sui.dns` | имя на сети `sui`, если оно не совпало с именем сервиса |
| `sui.probe` | адреса HTTP через запятую, например `http://billing:8080/health` |
| `sui.metrics.port` | порт `/metrics` |
| `sui.metrics.path` | путь, если это не `/metrics` |
| `sui.alert` | значение `page`, если падение проверки будит ночью |
| `sui.name` | как назвать сервис в Telegram |
| `sui.summary` | фраза, когда он лежит |
| `sui.resolved` | фраза, когда он снова жив |

Без `sui.alert=page` проверка и метрики есть, ночной Telegram молчит. Текст для друзей пишут по-русски и без имён контейнеров.

Метка попадает в контейнер только при создании. На уже запущенный контейнер её не наклеить: Docker так не умеет. После правки меток этот контейнер пересоздают. LiveKit при активном звонке не пересоздают.

Если контейнер с `sui.alert=page` исчез, проверка ещё 6 часов остаётся и падает. Потом запись забывается, чтобы удалённый сервис не будил вечно.

Пока у чата нет меток на самих контейнерах, его цели лежат в `platform/services.json`. Метка на контейнере важнее этой записи.

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

Имя `billing` в адресе проверки — это имя сервиса Compose на сети `sui`. Его должно быть видно с контейнера `blackbox`.

Служба systemd для такого проекта: `Type=oneshot`, `RemainAfterExit=yes`, `ExecStart=docker compose up -d`, `ExecStop=docker compose stop`. `down` в `ExecStop` не ставят: он удаляет контейнеры.

## Чат

Чат остаётся проектом `svoi`. Новый кусок самого чата, а не отдельного продукта, по-прежнему добавляют в `tailnet-messenger`. На приставке файл `deploy/compose.platform-net.yml` подмешивается в `make`, когда он лежит на месте, и присоединяет контейнеры чата к сети `sui`. Пока звонок идёт, `make up` не запускают: иначе LiveKit пересоздастся.

Флаг `SVOI_OBSERVE` на живой службе стоит `false`. Иначе следующий запуск чата поднимет второй Grafana и второй Prometheus на тех же портах.
