# Наблюдаемость

Профиль выключен, пока чат не нужен на экране. Включают командой `make observe` на хосте, где есть около 8 ГБ RAM. Команда поднимает `deploy/compose.observe.yml` и ещё раз вызывает `make serve`.

Grafana публикуется в tailnet только если `http://127.0.0.1:3000/login` уже отвечает. Адрес без отдельного порта: `https://<имя>/`. Пути `/_matrix`, `/_synapse` и `/.well-known` остаются у Synapse. Логин `admin`, пароль в `secrets/grafana_admin_password`. Регистрация и анонимный вход выключены.

Дашборд «Свои» открывается сразу. Свободные память и диск сверху. Список контейнеров не зашит: cAdvisor читает сокет Docker, Alloy читает логи всех контейнеров. Новый контейнер на этой машине появляется в графиках сам. Свои счётчики (как у Synapse или LiveKit) добавляются отдельной строкой в `deploy/observe/prometheus/prometheus.yml`, порт метрик на хост не публикуют.

Prometheus слушает `127.0.0.1:9091` на хосте и в tailnet через serve не выставляется.

## Что собирается

- Synapse: `synapse:9090/_synapse/metrics`. Порт метрик наружу хоста не проброшен, его видит только сеть Docker `edge`.
- blackbox с модуля `http_2xx` ходит на `http://synapse:8008/health` и на `/_matrix/client/versions`.
- node_exporter читает корень хоста через `/host` и текстовые файлы из `deploy/rendered/textfile`.
- cAdvisor, привилегированный, метрики всех контейнеров Docker на хосте, не только этого compose. Образ `v0.60.6`: на этом Docker образы лежат в containerd, и `v0.49` контейнеры не видел. Сокет containerd смонтирован только в этот контейнер.
- LiveKit: `livekit:6789`. Порт наружу хоста не проброшен.
- postgres-exporter на внутренней сети `data`, порт наружу хоста не проброшен.
- blackbox-apps ходит на Element Web, сервис токенов звонка, прокси звонков и LiveKit. Эти пробы не входят в алерт `HomeserverDown`.
- Loki хранит логи 168 часов, схема v13, файловый tsdb. Alloy читает сокет Docker и пишет в Loki. Сокет равносилен root. Это записано в модели угроз.
- Alertmanager. Пустой `ALERT_WEBHOOK_URL` оставляет получателя без интеграций. На публичный ntfy по умолчанию ничего не уходит.

Теги образов закреплены в `.env.example`. Ядро (Synapse, Element Web, Postgres) сверено с релизами. Теги Prometheus, Grafana, Loki, Alloy, Alertmanager, node_exporter, cAdvisor, blackbox, LiveKit и Caddy записаны явно. Если тег когда-нибудь не найдётся, падает только этот профиль, не чат. Перед первым `make observe` имеет смысл `docker compose -f deploy/compose.observe.yml --env-file .env config` и один pull.

## Алерты

Файл `deploy/observe/prometheus/alerts.yml`.

| Имя | Условие | Зачем |
| --- | --- | --- |
| `HomeserverDown` | `probe_success{job="blackbox-http"} == 0` дольше 2 минут | API не отвечает |
| `SynapseMetricsDown` | `up{job="synapse"} == 0` дольше 2 минут | Жив прокси, мертвы метрики Synapse, или наоборот |
| `DiskSpaceLow` | свободно меньше 15% на ext4, xfs, btrfs или zfs дольше 10 минут | Диск. На APFS Mac этот ряд может не появиться |
| `BackupStale` | локальная метрика старше 26 часов | Пропущенный почасовой бэкап с запасом |
| `OffsiteBackupStale` | offsite-метрика старше 26 часов | Срабатывает только после первого успешного restic. Свежая установка без restic из-за этого не красная |

Диск не привязан к одному `mountpoint="/"`, чтобы не пропустить отдельный том с Docker.

Текстовый файл `deploy/rendered/textfile/backup.prom` пишет `make backup`: временный файл и `rename`. Файл заканчивается переводом строки.

## Логи без Grafana

`make logs` показывает логи compose текущего набора файлов. На хосте также:

```bash
docker compose -f deploy/compose.yml --env-file .env ps
docker compose -f deploy/compose.yml --env-file .env logs --tail=200 synapse
```

Журнал контейнеров режется json-file, 10 МБ на файл, 5 файлов.
