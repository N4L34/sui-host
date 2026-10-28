# Эксплуатация

Способ запуска решает `AGENTS.md`. Здесь команды, которыми заходят на узел и проверяют уже поднятое.

Команды выполняют по Tailscale под пользователем `sui`. Пароль `sudo` в переписку и в git не пишут. `sudo -n` на узле без пароля не проходит.

## Вход

Маршрут рабочей станции до `100.64.0.10` может уйти в локальный шлюз. Тогда `ssh sui` и `ssh 100.64.0.10` зависают. Берут интерфейс `utun`, на котором висит адрес станции из `100.64.0.0/10`. Номер интерфейса каждый раз свой.

```bash
IFACE=$(ifconfig | awk '/^utun/{i=$1} /inet 100\./{gsub(":","",i); print i; exit}')
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o BindInterface="$IFACE" \
  -o HostKeyAlias=192.0.2.15 -o BatchMode=yes sui@100.64.0.10
```

`HostKeyAlias` нужен, потому что ключ хоста записан для `192.0.2.15`. Короткое имя `sui` без записи в `~/.ssh/config` резолвер системы может не найти.

Если привязать интерфейс нельзя:

```bash
ssh -o 'ProxyCommand=/Applications/Tailscale.app/Contents/MacOS/Tailscale nc %h %p' \
  sui@100.64.0.10
```

Пока `tailscale status` не показывает узел `sui` online, SSH нет и на узле ничего не запускают.

## Свой проект

Из каталога проекта:

```bash
docker compose up -d
docker compose ps
docker compose stop
```

Один сервис, соседей не пересоздавая:

```bash
docker compose up --no-deps -d <сервис>
```

`docker compose down` в `/opt/monitoring` и в чужом каталоге не вызывают: команда удаляет контейнеры. Флаг `--remove-orphans` не ставят.

`/home/sui/commands` и `/home/sui/avtobot` поднимают той же командой. Из этих каталогов не запускают `python3`, `bot.py`, `selftest.py` и проверку, которая создаёт пользователя. Второй процесс на тот же токен не держат.

`git push` сам файлы на узел не выкладывает. На узел уходят названные файлы через `rsync` по SSH. CI репозитория чата проверяет сборку и на узел не деплоит.

## Чат и мониторинг

`systemctl restart svoi.service` и `systemctl stop svoi.service` останавливают чат: `ExecStop` вызывает `make down`. Один контейнер чата перезапускают через `docker compose` из `/home/sui/tailnet-messenger`, тем же набором файлов, что у `make`. Проект Compose чата называется `chat`.

`make up` в каталоге чата вызывает `render_config.py` и пересоздаёт LiveKit. Во время звонка его не запускают. Полный `scripts/render_config.py` переписывает homeserver, Element, LiveKit и Caddy. На узле так не делают. Правят уже сгенерированный файл и перезапускают только контейнер, который его читает.

Мониторинг поднимает `monitoring.service` из `/opt/monitoring`. Остановка: `docker compose stop` в этом каталоге. У чата `SVOI_OBSERVE=false`. Иначе следующий запуск `svoi.service` поднимет второй Grafana. Флаг меняют в drop-in и ради этого `svoi.service` не перезапускают.

Пересоздание `chat-livekit-1` рвёт текущий звонок. `rsync` поверх файла, который уже примонтирован в контейнер, оставляет процесс на старом inode. Такой контейнер пересоздают точечно. LiveKit при активном звонке не трогают.

Наружу не публикуют `8448`, не включают федерацию, не вешают медиа на `0.0.0.0`. Docker `userland-proxy` не выключают и `dockerd` не перезапускают. Узел не перезагружают без явной задачи на это. Новых пользователей Linux не заводят. Подсветку панели не включают: её гасит `sui-dark-mode`.

Тома на диске `svoi_synapse-data` и `svoi_postgres-data` не удаляют и не переименовывают. В первом лежит ключ подписи. `docker volume prune`, `docker system prune --volumes` и `docker compose down -v` эти тома стирают.

## Адрес по имени

Сертификат выписан на `sui.example.com`. Проверяют имя:

```bash
curl -fsS -o /dev/null -w 'grafana %{http_code}\n' https://sui.example.com/
curl -fsS -o /dev/null -w 'matrix %{http_code}\n' https://sui.example.com/_matrix/client/versions
```

Корень имени отдаёт Grafana. `/_matrix/client/versions` отдаёт JSON. Element Web слушает `:8443`, звонки слушают `:8444`.

`https://100.64.0.10/` обрывается на рукопожатии: клиент не передаёт имя. `https://192.0.2.15/` не отвечает: порт `443` слушает только интерфейс Tailscale. Обычный `http://` на этот порт получает отказ. Ссылку человеку дают с именем и схемой `https`.

Контейнеры: `docker ps --format '{{.Names}} {{.Status}}'`.

## Секреты

В git не попадают `.env`, каталоги `secrets/`, `deploy/rendered/`, токен бота, дампы и архивы. Пример окружения с рабочей станции на узел как боевой конфиг не копируют.

Пароли сервисов чата лежат в `/home/sui/tailnet-messenger/secrets/` с режимом `600`. Пароль Grafana там же. Платформа читает этот файл только внутрь контейнера. Токен Telegram для Alertmanager лежит в `runtime/telegram_token` с режимом `644`: контейнер читает его как `nobody`. Каталог `runtime/` в git не входит. В yaml токена нет.

Приёмка чата и отклонённые схемы: `docs/spec/SPEC.md` и `docs/adr/` в `N4L34/tailnet-messenger`.
