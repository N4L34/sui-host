# Подключение сервиса

Способ запуска решает `AGENTS.md`. Этот файл даёт каркас, метки и пример Compose.

Новый сервис — папка в этом репозитории и проект Compose с тем же именем. Обычный сервис с `compose.yml` и `systemd/<имя>.service` автоматически попадает в CI и deploy; ручные списки менять не нужно. Нестандартный набор Compose-файлов объявляют в `<имя>/deploy/project.json`. На узле это `/home/sui/sui-host/<имя>`. В папки `monitoring` и `chat` его не добавляют. Сеть `chat_data` и том Postgres чата ему не отдают. Мониторинг в новый проект не копируют: он уже запущен службой `monitoring.service` из `/home/sui/sui-host/monitoring`.

## Каркас

Образ фиксируют тегом. Тега `latest` нет. `pull_policy: missing` оставляет на узле уже скачанный тег. Другой тег приезжает только явным `docker compose pull` этого сервиса.

У долгого сервиса:

- `restart: unless-stopped`;
- `security_opt: [no-new-privileges:true]`;
- `init: true`;
- журнал `json-file`, `max-size: "10m"`, `max-file: "5"`;
- `deploy.resources.limits.memory` с явным пределом;
- `user` равен пользователю, под которым собран образ. У официального образа берут пользователя из его документации. Поле не ставят наугад;
- `read_only: true` и `tmpfs` на `/tmp`, если процесс живёт без записи в корень контейнера. Образ, который без такой записи не стартует, оставляют с записью и пишут причину в ответе;
- `TZ=Europe/Moscow`, если процессу нужно московское время;
- секрет файлом режима `600`, не строкой в compose и не в git;
- если Compose требует переменные для интерполяции, добавляют `.env.example` с безопасными заглушками для CI; реальные значения хранят в исключённом из git `.env` или отдельном файле секрета;
- порт на хост только `127.0.0.1:хост:контейнер`;
- сеть `monitoring` с `external: true` и `name: monitoring`;
- `healthcheck` с `interval`, `timeout`, `retries` и `start_period`, если в образе есть команда проверки. Команду, которой в образе нет, не выдумывают.

На `0.0.0.0` и на порт `8448` сервис не сажают. `network_mode: host` не используют. Медиа LiveKit уже слушает адрес Tailscale. Новый проект этот адрес не занимает.

Одноразовая подготовка, которая завершается после работы, получает `restart: "no"`.

Лимит памяти из `deploy.resources.limits` Compose на одном узле применяет сам. Swarm для этого не поднимают.

## Метки

Память, CPU, перезапуски и журнал появляются сразу: cAdvisor и Alloy читают сокет Docker всего узла. Отдельный дашборд на контейнер не заводят. Общий список уже есть на `https://<имя-узла>/`.

Таймер `monitoring-discover.timer` раз в 15 секунд читает метки и пишет цели Prometheus. Для нового сервиса `prometheus.yml` не правят.

| Метка | Назначение |
| --- | --- |
| `monitoring.project` | имя проекта Compose. Если метки нет, берётся `com.docker.compose.project` |
| `monitoring.service` | имя сервиса внутри этого проекта, латиницей |
| `monitoring.dns` | имя на сети `monitoring`, если оно отличается от имени сервиса |
| `monitoring.probe` | URL HTTP через запятую, например `http://billing:8080/health` |
| `monitoring.metrics.port` | порт `/metrics` |
| `monitoring.metrics.path` | путь, если он отличается от `/metrics` |
| `monitoring.alert` | `page`, если падение проверки должно уйти в Telegram |
| `monitoring.name` | имя сервиса в тексте алерта |
| `monitoring.summary` | текст, когда проверка упала |
| `monitoring.resolved` | текст, когда проверка снова успешна |

Без `monitoring.alert=page` проверка и метрики есть, page-алерта нет. Текст алерта пишут по-русски и без имён контейнеров.

Имя проекта Compose — отдельная метка `project` на проверке и на `/metrics`. Два проекта с сервисом `api` остаются двумя целями: ключ `billing/api` и `shop/api`. Второй Prometheus и вторая Grafana для этого не поднимаются. На дашборде `Свои` сверху выбирается проект, и контейнеры, проверки и память этого Compose показываются отдельно от остальных. В Telegram перед текстом алерта пишется имя проекта.

Метка попадает на контейнер при создании. У уже запущенного контейнера Docker её не меняет. После правки меток контейнер пересоздают командой из раздела ниже. `chat-livekit-1` при активном звонке не пересоздают.

Если контейнер с `monitoring.alert=page` удалён, проверка ещё 6 часов остаётся и падает. Затем запись снимается.

Пока у контейнеров чата нет меток, их цели записаны в `monitoring/platform/services.json`. Метка на контейнере перекрывает эту запись. Новый сервис в этот файл не дописывают.

## Пример

`user: "1000:1000"` в примере — пользователь, под которым собран этот образ. Для другого образа ставят его пользователя.

```yaml
services:
  billing:
    image: ghcr.io/example/billing:1.4.2
    pull_policy: missing
    user: "1000:1000"
    init: true
    read_only: true
    tmpfs:
      - /tmp
    restart: unless-stopped
    security_opt: [no-new-privileges:true]
    environment:
      TZ: Europe/Moscow
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
    ports:
      - "127.0.0.1:8090:8080"
    networks:
      - monitoring
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    labels:
      monitoring.project: billing
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

Имя `billing` в URL проверки — имя сервиса Compose на сети `monitoring`. Оно резолвится из контейнера `blackbox`. Команда healthcheck — та, что есть в образе.

## Команды

Из каталога проекта на рабочей станции:

```bash
docker compose config
```

На узле проект поднимают и смотрят так:

```bash
docker compose up -d
docker compose ps
```

Один сервис обновляют без соседей:

```bash
docker compose up --no-deps -d billing
```

Свой проект останавливают командой `docker compose stop` в его каталоге. В `ExecStop` юнита слова `down` нет: команда удаляет контейнеры.

Юнит systemd для проекта копирует `systemd/monitoring.service`: `Type=oneshot`, `RemainAfterExit=yes`, `User=sui`, `WorkingDirectory` равен каталогу проекта, `ExecStart=/usr/bin/docker compose up -d`, `ExecStop=/usr/bin/docker compose stop`.

`rsync` поверх файла, который уже смонтирован в контейнер, оставляет процесс на старом inode. Такой контейнер пересоздают точечно, командой `up --no-deps -d` на этот сервис.

## Чат

Чат лежит в `/home/sui/sui-host/chat`. Проект Compose называется `chat`: это задаёт `deploy/compose.platform-net.yml`. Служба systemd называется `svoi.service`. `make up` на узле не запускают: он вызывает `render_config.py` и пересоздаёт LiveKit. Во время звонка чат не пересоздают.

`SVOI_OBSERVE` у `svoi.service` равен `false`. Иначе следующий запуск чата поднимет второй Grafana и второй Prometheus на тех же портах.
