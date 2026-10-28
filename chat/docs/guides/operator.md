# Оператор «Свои»

«Свои» — это чат на homeserver Synapse. Друзья ходят в него через Element X на iPhone и Android, через Element Desktop на Mac или через Element Web на этом же сервере. Сервер виден только в tailnet. Публичный порт для чата не открывайте.

Имя сервера берётся из MagicDNS этого узла. В примерах ниже оно записано как `messenger.example.com`. Подставьте имя, которое показывает `tailscale status`. После первого `make render` с настоящим именем менять его нельзя. Пример с `tailnet-name` штамп не ставит.

## Какой хост выбрать

Нужен один хост, который не засыпает. Ноутбук, который гасит крышку, роняет чат. Подойдёт уже существующий VPS, на котором живут и другие сервисы. Этот репозиторий не ставит файрвол и не закрывает чужие порты.

На машине должны быть Docker и Tailscale. Как вы их ставите на этом VPS, так и оставляйте. Чат слушает `127.0.0.1`, а снаружи tailnet его показывает `tailscale serve`.

Узел чата пометьте `tag:messenger`. В политику Tailscale добавьте доступы из `deploy/tailscale/acl.hujson`. Если в tailnet есть другие машины, не заменяйте всю политику этим файлом, допишите только grant для `tag:messenger`.

Клонируйте репозиторий туда, где вам удобно. Юниты systemd ниже рассчитаны на `/opt/tailnet-messenger`. Если каталог другой, поправьте `WorkingDirectory` в юнитах.

### Mac, который не спит

Поставьте Docker Desktop или OrbStack и приложение Tailscale в строке меню. Включите Tailscale и оставьте его в состоянии Connected. Отключите сон: Mac не должен засыпать при выключенном экране и при закрытой крышке.

Каталог репозитория может быть любым. Юнитов systemd на Mac нет, поэтому после перезагрузки проверьте `tailscale serve status`. Если маршрутов HTTPS нет, снова выполните `make serve`.

## Первая установка

Команды выполняйте в каталоге репозитория на хосте чата.

1. Узнайте MagicDNS-имя этого узла:

```bash
tailscale status
```

Нужно полное имя, которое использует и `tailscale cert`. Оно похоже на `messenger.example.com`. Буквы только строчные, схемы `https://` в имени нет.

2. Скопируйте пример окружения и впишите это имя:

```bash
cp .env.example .env
```

Откройте `.env` и задайте `MATRIX_SERVER_NAME`. Это имя станет именем сервера Matrix, например `@operator:messenger.example.com`. Позже его не меняют.

Пример `messenger.example.com` можно отрендерить для проверки: штамп `secrets/server_name` он не пишет. Настоящее имя фиксируется первым `make render` уже с MagicDNS. Учётные записи создавайте только после этого.

3. Создайте секреты:

```bash
make init
```

Команда создаёт `secrets/` и не трогает уже существующие файлы. Если Tailscale установлен, она печатает MagicDNS-имя. Сверьте его с `.env`.

4. Соберите конфиг, поднимите стек и опубликуйте его в tailnet:

```bash
make render
make up
make serve
make smoke
```

`make serve` ничего не открывает на публичном интерфейсе. Он вешает HTTPS Tailscale на локальные порты. В выводе должны быть адрес Synapse `https://messenger.example.com` и Element Web `https://messenger.example.com:8443`.

`make smoke` должен закончиться строкой `smoke passed`. Пока этого нет, учётные записи не создавайте.

На VPS после успешного `make smoke` поставьте юниты и включите автозапуск. Файлы рассчитаны на каталог `/opt/tailnet-messenger`:

```bash
cp deploy/systemd/svoi.service \
  deploy/systemd/svoi-backup.service \
  deploy/systemd/svoi-backup.timer \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now svoi.service
systemctl enable --now svoi-backup.timer
```

## Учётные записи

Регистрация через клиент закрыта. Человек сам себе аккаунт не создаёт. Вы создаёте его и говорите имя и одноразовый пароль лично или в канале, который не является этим чатом.

Администратор:

```bash
make admin USER=operator
```

Друг:

```bash
make user USER=alice
```

Команда печатает `@alice:messenger.example.com` и пароль. Передайте их и попросите сменить пароль после первого входа. Не оставляйте пароль в общем скриншоте и в истории shell на чужом экране.

Другу на iPhone отдайте `docs/guides/iphone.md`. Для Mac — `docs/guides/mac.md`. Android описан в `docs/guides/android.md`, этим путём почти не пользуются.

## Комната «Друзья»

Войдите от администратора и создайте комнату. Имя по умолчанию уже «Друзья»:

```bash
make room ADMIN=operator ADMIN_PASSWORD='текущий пароль'
```

Команда печатает идентификатор комнаты. Пригласите друга:

```bash
python3 scripts/matrix_admin.py invite \
  --admin operator \
  --password 'текущий пароль' \
  --room '!комната:messenger.example.com' \
  --user alice
```

Идентификатор комнаты берите в кавычки. Обе команды записывают пароль в историю shell. Попросите alice принять приглашение в Element.

## Копии

Сразу после первой учётной записи снимите копию и проверьте, что дамп открывается:

```bash
make backup
make drill
```

`make backup` пишет каталог `backups/<время UTC>/`. В нём три части: `postgres.dump`, `synapse-data.tar` (ключ подписи и медиа) и `secrets.tar` (секреты, `.env` и сгенерированный конфиг).

`make drill` поднимает временный Postgres и проверяет последний `postgres.dump`. Живую базу он не трогает. Ключ подписи drill не проверяет. Полное восстановление описано в `docs/runbooks/restore.md`.

На VPS таймер `svoi-backup.timer` запускает `make backup` каждый час. Скрипт оставляет 14 последних каталогов. Каталог `backups/` забирайте туда же, куда складываете остальные копии этого сервера. Смотрите `docs/runbooks/disk-full.md`.

На Mac таймера нет. Запускайте `make backup` сами.

## Где лежат секреты

- `.env` хранит имя сервера, теги образов и флаги. Права после `make init` — `600`.
- `secrets/` хранит пароль Postgres, секреты Synapse, пароль Grafana, пароль restic, секрет LiveKit, адрес Tailscale IPv4 и штамп `secrets/server_name`. Каталог имеет права `700`, файлы — `600`.
- Том Docker `svoi_synapse-data` хранит `/data/signing.key` и каталог медиа.
- Том `svoi_postgres-data` хранит базу.
- `deploy/rendered/homeserver.yaml` содержит пароль базы и другие секреты. Его пишет `make render`.
- `backups/` содержит дамп, ключ подписи и архив секретов.

Штамп `secrets/server_name` появляется при первом `make render` с настоящим именем, не с примером `tailnet-name`. Если потом изменить `MATRIX_SERVER_NAME`, `make render` остановится. Верните старое имя. Новое имя — это уже другой сервер.

## Что не коммитить

Git уже игнорирует `.env`, `secrets/`, `deploy/rendered/`, `backups/`, `*.dump` и `*.tar`. Всё равно не добавляйте их в коммит.

Не коммитьте одноразовые пароли людей. Ключ подписи не копируйте в чат и не кладите в репозиторий.
