# Сервисы

Состав узла на 28 октября 2026. Образы мониторинга закреплены в `.env.example` этого репозитория. Образы чата закреплены в compose репозитория `N4L34/tailnet-messenger`. Этот файл — инвентарь, а не второй источник тегов.

Долгий проект — контейнер своего Compose. У каждого Compose свой срез мониторинга: метка `project` на проверках и фильтр на дашборде `Свои`. Чат — `chat`, платформа — `monitoring`. Короткое задание, которое завершается, остаётся таймером: discover, резервная копия, утренняя сводка, проверка края. `avtobot.service` и `svoi-telegram-bot.service` уже работают вне Docker. Новым проектом эту форму не копируют и в этом изменении в контейнер их не переносят.

## Хост

| Служба | Назначение |
| --- | --- |
| `docker.service` | контейнеры, включена при загрузке |
| `tailscaled.service` | адрес `100.64.0.10` и Serve |
| `svoi.service` | Compose чата из `/home/sui/tailnet-messenger`, без стека наблюдения. Остановка вызывает `make down` |
| `monitoring.service` | метрики, журнал и алерты из `/opt/monitoring` |
| `monitoring-discover.timer` | раз в 15 секунд обновляет цели Prometheus по меткам и завершается |
| `svoi-backup.timer` | резервная копия чата раз в час и завершается |
| `svoi-edge-probe.timer` | раз в минуту проверяет чат и звонки через Tailscale и завершается |
| `svoi-telegram-bot.service` | долгий процесс команд вне Docker. Образцом нового проекта не служит |
| `svoi-telegram-morning.timer` | сводка в 05:00 UTC, 08:00 Europe/Moscow, и завершается |
| `svoi-telegram-week.timer` | сводка в воскресенье 08:00 UTC, 11:00 Europe/Moscow, и завершается |
| `sui-dark-mode.service` | гасит панель устройства |
| `avtobot.service` | долгий процесс вне Docker. Образцом нового проекта не служит |

`svoi.service` смотрит в `/home/sui/tailnet-messenger`. Файл в репозитории чата всё ещё указывает `WorkingDirectory=/opt/tailnet-messenger`. Поверх `/etc/systemd/system/svoi.service` его не копируют.

Календари systemd на этом узле считаются в UTC. Поле `Timezone=` не действует.

## Чат

Проект Compose `chat`. Каталог `/home/sui/tailnet-messenger`. Код в `N4L34/tailnet-messenger`. Имя проекта задаёт префикс контейнеров. Служба `svoi.service` называется по-старому. Теги сверяют с compose того репозитория.

| Контейнер | Образ | Публикация |
| --- | --- | --- |
| `chat-synapse-1` | `ghcr.io/element-hq/synapse:v1.163.0` | `127.0.0.1:8008` |
| `chat-postgres-1` | `postgres:17-alpine` | только сеть `chat_data` |
| `chat-element-web-1` | `ghcr.io/element-hq/element-web:v1.12.32` | `127.0.0.1:8080` |
| `chat-livekit-1` | `livekit/livekit-server:v1.13.10` | `127.0.0.1:7880`, медиа на `100.64.0.10` |
| `chat-lk-jwt-1` | `ghcr.io/element-hq/lk-jwt-service:latest` | токены звонка, сеть `chat_edge` |
| `chat-calls-proxy-1` | `caddy:2.10-alpine` | `127.0.0.1:7882`, Serve `:8444` |
| `chat-call-dial-1` | `caddy:2.10-alpine` | внутренний набор для сервиса токенов, порта на хосте нет |

Тег `latest` у `chat-lk-jwt-1` — известный долг этого контейнера. Новый образ так не подписывают.

`synapse-data-init` в списке процессов не остаётся. Он выставляет права на каталог данных и завершается.

Тома на диске остаются `svoi_synapse-data` и `svoi_postgres-data`. В первом лежит ключ подписи сервера. Каталоги не переименовывают и не копируют.

LiveKit оставляют на `v1.13.10`. Образ `v1.9.0` не обслуживал `/rtc/v1`, и звонок обрывался примерно через 15 секунд. На текущем образе `/rtc/v1` отвечает 404, а `/rtc/v1/validate` без токена отвечает 401. Для `v1.13.10` это штатный ответ. `use_ice_lite: true` во время тех обрывов уже был включён, его не снимают.

Сервис токенов пока отдельный контейнер, не приложение Synapse. Запрос Element X `delegate_delayed_leave` на homeserver отвечает 404, и при обрыве связи сервер звонок сам не закрывает. В рабочей копии `tailnet-messenger` это размечено и на узел не выкатывалось.

Регистрация закрыта. Учётные записи создаёт оператор или бот команд. Локальные части `operator` и `svoibot` не удаляют и не сбрасывают. Пароли в этот репозиторий не входят.

## Мониторинг

Проект Compose `monitoring`, каталог `/opt/monitoring`, служба `monitoring.service`. Теги сверяют с `.env.example`. Цели Prometheus обновляет `monitoring-discover.timer`. История графиков и журнала лежит во внешних томах `svoi_prom-data`, `svoi_grafana-data`, `svoi_loki-data`, `svoi_alert-data`, `svoi_alloy-data`. Имена этих томов оставлены: внутри история, и новый пустой том её не заменит.

| Контейнер | Образ в `.env.example` | Роль |
| --- | --- | --- |
| `monitoring-grafana-1` | `grafana/grafana:12.0.2` | интерфейс, `127.0.0.1:3000` |
| `monitoring-prometheus-1` | `prom/prometheus:v3.4.0` | метрики, `127.0.0.1:9091` |
| `monitoring-alertmanager-1` | `prom/alertmanager:v0.28.0` | page-алерты |
| `monitoring-loki-1` | `grafana/loki:3.4.2` | журнал |
| `monitoring-alloy-1` | `grafana/alloy:v1.8.1` | сбор логов контейнеров, сокет Docker |
| `monitoring-cadvisor-1` | `ghcr.io/google/cadvisor:v0.60.6` | память, CPU, перезапуски, `privileged` |
| `monitoring-node-exporter-1` | `prom/node-exporter:v1.9.0` | диск и узел |
| `monitoring-postgres-exporter-1` | `prometheuscommunity/postgres-exporter:v0.17.1` | база чата |
| `monitoring-blackbox-1` | `prom/blackbox-exporter:v0.25.0` | HTTP-проверки в сети `monitoring` |

Сокет Docker у Alloy и `privileged` у cAdvisor остаются исключением мониторинга. Новому проекту их не копируют.

Page-алерт уходит, когда чат не отвечает, сервис с `monitoring.alert=page` не отвечает, на диске свободно меньше 15%, локальная или внешняя копия старше 26 часов, ядро завершило процесс по памяти, по корню Serve открылся не Matrix, звонки не готовы, остановилась проверка края или платформа перестала видеть свои компоненты. Повтор раз в 4 часа. Обрыв одного звонка, строка в Loki и короткий всплеск CPU в этот канал не входят. Проверка без `page` остаётся на графике.

Сводка утром в 08:00 Europe/Moscow, недельная в воскресенье в 11:00 Europe/Moscow. В тексте нет имён контейнеров.

## avtobot

Каталог `/home/sui/avtobot`, служба `avtobot.service`, пользователь `sui`. В Docker не входит и к чату не подключён. Состояние хранит в `history.sqlite`. Локальный прокси слушает `127.0.0.1:10818`. Конфиг лежит в `secrets/` этого каталога и в git не копируется.

## Доступ

SSH на узел идёт пользователем `sui` через Tailscale. Доступ к репозиторию сам по себе вход на узел не даёт. Команды входа: `docs/work.md`.
