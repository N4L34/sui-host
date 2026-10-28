# Эксплуатация

Команды на узле выполняют по Tailscale под пользователем `sui`. Пароль `sudo` в переписку и в git не пишут. `sudo -n` на узле без пароля не проходит.

## Вход

Маршрут рабочей станции до `100.64.0.10` может уйти в локальный шлюз, а не в интерфейс Tailscale. Тогда `ssh sui` и `ssh 100.64.0.10` зависают. Берут интерфейс `utun`, на котором висит адрес станции из `100.64.0.0/10`. Номер интерфейса не фиксирован.

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

Пока Tailscale на станции выключен или узел `sui` не в сети, SSH нет. Сначала смотрят `tailscale status`: узел должен быть online.

## Ограничения

`systemctl restart svoi.service` и `systemctl stop svoi.service` останавливают чат: `ExecStop` вызывает `make down`. Один контейнер перезапускают через `docker compose` из `/home/sui/tailnet-messenger`, тем же набором файлов, что у `make`. Флаг `--remove-orphans` не ставят.

Платформу поднимает `sui-platform.service` из `/home/sui/sui-host`. Остановка платформы: `docker compose stop` в этом каталоге. `docker compose down` там не вызывают. У чата `SVOI_OBSERVE=false`. Иначе следующий запуск `svoi.service` поднимет второй Grafana. Флаг меняют в drop-in и ради этого `svoi.service` не перезапускают.

Пересоздание `svoi-livekit-1` рвёт текущий звонок. Полный `scripts/render_config.py` переписывает homeserver, Element, LiveKit и Caddy. На узле так не делают. Правят уже сгенерированный файл и перезапускают только контейнер, который его читает.

`rsync` поверх файла, который уже примонтирован в контейнер, оставляет процесс на старом inode. Такой контейнер пересоздают точечно. LiveKit при активном звонке не трогают.

Наружу не публикуют `8448`, не включают федерацию, не вешают медиа на `0.0.0.0`. Docker `userland-proxy` не выключают и `dockerd` не перезапускают. Узел не перезагружают без явной задачи на это. Новых пользователей Linux не заводят. Подсветку панели не включают: её гасит `sui-dark-mode`.

`git push` сам ничего на узел не выкладывает. На узел уходят названные файлы через `rsync` по SSH. CI репозитория чата проверяет сборку и на узел не деплоит.

## Секреты

В git не попадают `.env`, каталоги `secrets/`, `deploy/rendered/`, токен бота, дампы и архивы. Пример окружения с рабочей станции на узел как боевой конфиг не копируют.

Пароли сервисов чата лежат в `/home/sui/tailnet-messenger/secrets/` с режимом `600`. Пароль Grafana там же. Платформа читает этот файл только внутрь контейнера. Токен Telegram для Alertmanager лежит в `runtime/telegram_token` с режимом `644`: контейнер читает его как `nobody`. Каталог `runtime/` в git не входит. В yaml токена нет.

## Проверка

Чат: `https://sui.example.com/_matrix/client/versions` отдаёт JSON. Корень того же имени отдаёт Grafana. Element Web слушает Tailscale `:8443`. Звонки слушают `:8444`.

Контейнеры: `docker ps --format '{{.Names}} {{.Status}}'`. Журнал бота команд: `journalctl -u svoi-telegram-bot.service`. Журнал `avtobot`: `journalctl -u avtobot.service`.

Приёмка чата и отклонённые схемы: `docs/spec/SPEC.md` и `docs/adr/` в `N4L34/tailnet-messenger`.
