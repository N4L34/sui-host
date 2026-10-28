# Сервисы

Снимок 28 октября 2026. Контейнеры чата к этому моменту работали четвёртые сутки. Образы ниже те, что реально запущены.

## Хост

| Служба | Зачем |
| --- | --- |
| `docker.service` | контейнеры, включена при загрузке |
| `tailscaled.service` | сеть `100.64.0.10` и Serve |
| `svoi.service` | поднимает Compose чата из `/home/sui/tailnet-messenger`, без стека наблюдения |
| `sui-platform.service` | поднимает метрики, журнал и алерты из `/home/sui/sui-host` |
| `sui-discover.timer` | раз в 15 секунд обновляет цели Prometheus по меткам контейнеров |
| `svoi-backup.timer` | снимок чата раз в час |
| `svoi-edge-probe.timer` | раз в минуту проверяет, что с хвоста Tailscale чат и звонки отвечают |
| `svoi-telegram-bot.service` | команды людей и комнат в личке Telegram |
| `svoi-telegram-morning.timer` | сводка в 05:00 UTC, это 08:00 по Москве |
| `svoi-telegram-week.timer` | сводка в воскресенье 08:00 UTC, это 11:00 по Москве |
| `sui-dark-mode.service` | гасит подсветку экрана и индикаторы SuiPlay |
| `avtobot.service` | бот госномеров, не контейнер |

Живой юнит `svoi.service` смотрит в `/home/sui/tailnet-messenger`. Файл в репозитории чата всё ещё пишет `WorkingDirectory=/opt/tailnet-messenger`. Его не копируют поверх `/etc/systemd/system/svoi.service`. `ExecStop` у этой службы вызывает `make down` и гасит весь чат.

Календари systemd на этой машине считаются в UTC. Поле `Timezone=` тут не работает.

## Чат

Проект Compose `svoi`. Каталог `/home/sui/tailnet-messenger`. Код в `N4L34/tailnet-messenger`.

| Контейнер | Образ | Куда смотрит |
| --- | --- | --- |
| `svoi-synapse-1` | `ghcr.io/element-hq/synapse:v1.163.0` | `127.0.0.1:8008` |
| `svoi-postgres-1` | `postgres:17-alpine` | только сеть `data` |
| `svoi-element-web-1` | `ghcr.io/element-hq/element-web:v1.12.32` | `127.0.0.1:8080` |
| `svoi-livekit-1` | `livekit/livekit-server:v1.13.10` | `127.0.0.1:7880`, медиа на `100.64.0.10` |
| `svoi-lk-jwt-1` | `ghcr.io/element-hq/lk-jwt-service:latest` | токены звонка, внутри сети `edge` |
| `svoi-calls-proxy-1` | `caddy:2.10-alpine` | `127.0.0.1:7882`, его забирает Serve `:8444` |
| `svoi-call-dial-1` | `caddy:2.10-alpine` | внутренний набор для сервиса токенов, порта на хосте нет |

Одноразовый `synapse-data-init` в списке процессов не висит. Он выставляет права на каталог данных и завершается.

Тома `svoi_synapse-data` и `svoi_postgres-data` не удаляют. В первом ключ подписи сервера.

LiveKit оставляют на `v1.13.10`. Образ `v1.9.0` не умел маршрут `/rtc/v1`, из-за этого звонок обрывался примерно через 15 секунд. На текущем образе голый `/rtc/v1` отвечает 404, а `/rtc/v1/validate` без токена отвечает 401. Это норма для `v1.13.10`. `use_ice_lite: true` во время тех обрывов уже стоял, его не снимают.

Сервис токенов пока отдельный контейнер, не приложение Synapse. Поэтому запрос Element X `delegate_delayed_leave` на homeserver отвечает 404, и при обрыве связи сервер сам звонок не закрывает. В рабочей копии `tailnet-messenger` это размечено, на приставку не выложено.

Учётные записи Matrix заводит хозяин или бот. Открытой регистрации нет. Сейчас есть `@operator` (админ), `@sergon`, `@elina`, `@serzh` (это не Сергон), `@ping` (проверка, не друг), `@svoibot` (служебный админ бота). Пароли в этот репозиторий не входят. `@operator` и `@svoibot` не удаляют и не сбрасывают.

## Платформа

Отдельный проект Compose `sui`, каталог `/home/sui/sui-host`, служба `sui-platform.service`. Цели Prometheus обновляет `sui-discover.timer`. Образы те же, что были у наблюдения внутри чата. История графиков и журнала лежит в прежних томах `svoi_prom-data`, `svoi_grafana-data`, `svoi_loki-data`, `svoi_alert-data`, `svoi_alloy-data`.

| Контейнер | Образ | Роль |
| --- | --- | --- |
| `sui-grafana-1` | `grafana/grafana:12.0.2` | страницы, `127.0.0.1:3000` |
| `sui-prometheus-1` | `prom/prometheus:v3.4.0` | метрики, `127.0.0.1:9091` |
| `sui-alertmanager-1` | `prom/alertmanager:v0.28.0` | ночные алерты |
| `sui-loki-1` | `grafana/loki:3.4.2` | журнал |
| `sui-alloy-1` | `grafana/alloy:v1.8.1` | забирает логи всех контейнеров |
| `sui-cadvisor-1` | `ghcr.io/google/cadvisor:v0.60.6` | память, CPU, перезапуски |
| `sui-node-exporter-1` | `prom/node-exporter:v1.9.0` | диск и машина |
| `sui-postgres-exporter-1` | `prometheuscommunity/postgres-exporter:v0.17.1` | база чата |
| `sui-blackbox-1` | `prom/blackbox-exporter:v0.25.0` | HTTP-проверки в сети `sui` |

Ночной будильник: чат не отвечает, сервис с меткой `sui.alert=page` не отвечает, на диске свободно меньше 15%, локальная или внешняя копия старше 26 часов, ядро убило процесс по памяти, по корню Serve открылась не Matrix, звонки не готовы, пропала проверка края, мониторинг перестал видеть сам себя. Повтор раз в 4 часа. Обычный обрыв звонка, строка в Loki и короткий всплеск CPU туда не входят. Проверка без метки `page` остаётся на графике и ночью молчит.

Утренняя сводка в 08:00 по Москве, недельная в воскресенье в 11:00 по Москве. Текст для друзей, без имён контейнеров.

## Бот госномеров

Каталог `/home/sui/avtobot`, служба `avtobot.service`, пользователь `sui`. В Telegram ему присылают госномер, бот показывает карточку и помнит повтор в `history.sqlite`. Это не часть чата «Свои» и не контейнер.

Рядом процесс `/home/sui/avtobot/bin/xray` слушает только `127.0.0.1:10818`. Своей службы systemd у него нет, его поднимает бот. Конфиг лежит в `secrets/` этого каталога и в git не копируется.

## Люди в Tailscale

Хозяин заходит с Мака `macbook-pro-operator` и с телефонов. SSH на приставку остаётся у хозяина. Новому разработчику доступ к репозиторию не даёт вход на машину, пока хозяин не добавит его отдельно.
