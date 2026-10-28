# Как работать

Хозяин машины Оператор Латышев, GitHub `N4L34`. Команды на приставке выполняют с его Мака. Пароль `sudo` в чат и в git не пишут. `sudo -n` на `sui` без пароля не проходит.

## Вход

С Мака маршрут до `100.64.0.10` часто уходит в домашний роутер, а не в Tailscale. Тогда обычные `ssh sui` и `ssh 100.64.0.10` зависают. Номер интерфейса `utun` меняется. Берут тот, на котором висит адрес Мака из `100.64.0.0/10`.

```bash
IFACE=$(ifconfig | awk '/^utun/{i=$1} /inet 100\./{gsub(":","",i); print i; exit}')
ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o BindInterface="$IFACE" \
  -o HostKeyAlias=192.0.2.15 -o BatchMode=yes sui@100.64.0.10
```

`HostKeyAlias` нужен, потому что ключ хоста записан для `192.0.2.15`. Короткое имя `sui` без записи в `~/.ssh/config` система может не найти.

Если интерфейс связать нельзя:

```bash
ssh -o 'ProxyCommand=/Applications/Tailscale.app/Contents/MacOS/Tailscale nc %h %p' \
  sui@100.64.0.10
```

Пока Tailscale на Маке выключен или приставка не в tailnet, SSH нет. Обычный интернет это не заменяет. Сначала `tailscale status`: узел `sui` должен быть online.

## Чего не делать

`systemctl restart svoi.service` и `systemctl stop svoi.service` гасят чат: остановка вызывает `make down`. Один контейнер перезапускают через `docker compose` из `/home/sui/tailnet-messenger`, теми же файлами, что `make`. Флаг `--remove-orphans` не ставят.

Платформу поднимает `sui-platform.service` из `/home/sui/sui-host`. Её остановка — `docker compose stop` в этом каталоге. `docker compose down` там не вызывают. У чата `SVOI_OBSERVE=false`: следующий запуск `svoi.service` не должен поднять второй Grafana. Этот флаг меняют в drop-in, саму службу чата ради флага не перезапускают.

Пересоздание `svoi-livekit-1` рвёт текущий звонок. Полный `scripts/render_config.py` на приставке переписывает homeserver, Element, LiveKit и Caddy. На живой машине так не делают. Правят нужный уже сгенерированный файл и перезапускают только контейнер, который его читает.

`rsync` поверх файла, который уже примонтирован в контейнер, оставляет процесс на старом inode. Такой контейнер пересоздают точечно. LiveKit при активном звонке не трогают.

Наружу не публикуют `8448`, не включают федерацию, не вешают медиа на `0.0.0.0`. Docker `userland-proxy` не выключают и `dockerd` не перезапускают. Машину не перезагружают без просьбы хозяина. Новых пользователей Linux не заводят. Подсветку экрана включает только хозяин: её гасит `sui-dark-mode`.

`git push` сам ничего на приставку не выкладывает. На сервер уезжают названные файлы через `rsync` по SSH. CI репозитория чата тоже только проверяет, не деплоит.

## Секреты

В git не попадают `.env`, каталоги `secrets/`, `deploy/rendered/`, токен бота, дампы и архивы. Пример с Мака на приставку как боевой конфиг не копируют.

Пароли сервисов чата лежат в `/home/sui/tailnet-messenger/secrets/` с режимом `600`. Пароль Grafana там же, платформа читает этот файл только внутрь контейнера. Токен Telegram для Alertmanager лежит в `runtime/telegram_token` проекта `sui-host` с режимом `644`, потому что контейнер читает его как `nobody`. Каталог `runtime/` в git не входит. В yaml токена нет.

## Куда смотреть, если сломалось

Живой чат: `https://sui.example.com/_matrix/client/versions` должен быть JSON. Страницы: корень того же имени, это Grafana. Element Web: порт `8443`. Звонки: порт `8444`.

Контейнеры: `docker ps --format '{{.Names}} {{.Status}}'`. Журнал бота команд: `journalctl -u svoi-telegram-bot.service`. Журнал госномеров: `journalctl -u avtobot.service`.

Подробности чата, приёмка и отказы от других схем: `docs/spec/SPEC.md` и `docs/adr/` в `N4L34/tailnet-messenger`.
