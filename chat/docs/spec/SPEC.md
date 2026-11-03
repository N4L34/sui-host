# Спецификация «Свои»

Закрытый чат для небольшой группы. Люди ставят Tailscale, заходят в одну сеть и пишут в Element. Homeserver стоит на одной машине хозяина. В публичный интернет он не слушает.

Документ описывает то, что репозиторий уже умеет собирать. Решения, от которых отказались, лежат в `docs/adr/`. Правила разработки чата приведены в `docs/spec/AGENT.md`.

## Кто этим пользуется

Основной клиент — iPhone и Element X из App Store. Иногда Mac: Element Desktop для обычной работы, Element Web с этого же сервера, если приложение ставить не нужно. Android бывает редко: Element X из Play Store и always-on VPN в Tailscale.

Учётные записи заводит только хозяин. Человек получает имя и одноразовый пароль лично или в другом канале. Сам он на `/register` не регистрируется.

Ориентир по размеру — от двух до пятнадцати человек. Перед приглашением большой группы сверьте актуальные ограничения тарифного плана [Tailscale](https://tailscale.com/pricing) или разверните Headscale. Headscale в этот репозиторий вторым стеком не входит. Смотрите `docs/guides/headscale.md`.

## Что система делает

- Принимает сообщения, файлы и голосовые сообщения в зашифрованных комнатах.
- Показывает Element Web по адресу `https://<имя>:8443`.
- Будит iPhone и Android через шлюз пушей Element. Текст уведомления телефон забирает сам, и только если Tailscale на нём включён.
- Делает почасовой снимок базы, ключа подписи и медиа.
- По желанию включает групповые звонки и отдельный профиль Grafana.

## Что система не делает

- Не открывает регистрацию и не пускает гостей.
- Не федератится с matrix.org и любыми другими homeserver.
- Не слушает 80, 443 или 8448 на публичном адресе VPS.
- Не ставит свой Sygnal и не просит ключ APNs.
- Не собирает свой клиент Element.
- Не обещает два узла, Kubernetes и автоматический failover.
- Не кладёт текст сообщения в шлюз пушей.

## Имя сервера

`MATRIX_SERVER_NAME` — это MagicDNS-имя узла, на котором крутится Synapse. Буквы строчные, без схемы и без порта. Пример в репозитории: `messenger.example.com`. Настоящее имя печатает `make init`, если Tailscale уже установлен.

Это имя становится суффиксом `@alice:<имя>` и больше не меняется. Первый `make render` с настоящим именем пишет `secrets/server_name`. Следующая попытка подставить другое имя останавливает рендер. Пример с подстрокой `tailnet-name` или суффиксом `.example.ts.net` штамп не ставит, чтобы проверка CI не запирала оператора.

Учётные записи создают только после рендера настоящего имени. Иначе люди получат адреса на несуществующем сервере.

## Как устроен доступ

```text
iPhone / Mac / Android
        |
        | Tailscale, сертификат *.ts.net
        v
tailscale serve на хосте
        |
        +-- :443 /            -> 127.0.0.1:3000   Grafana, если она отвечает
        +-- :443 /_matrix     -> 127.0.0.1:8008   Synapse
        +-- :443 /_synapse    -> 127.0.0.1:8008   Synapse
        +-- :443 /.well-known -> 127.0.0.1:8008   Synapse
        +-- :8443  -> 127.0.0.1:8080   Element Web
        +-- :8444  -> 127.0.0.1:7882   звонки, только если ENABLE_CALLS=true
```

Synapse и Element публикуют порты только на `127.0.0.1`. Postgres сидит в сети Docker `data` с `internal: true` и наружу не ходит. Порт федерации 8448 не публикуется.

Хост может быть обычным VPS с другими сервисами и своим файрволом. Этот стек правила хоста не меняет. Публичный адрес машины не становится адресом чата: снаружи tailnet чат открывает только `tailscale serve`.

Узел чата помечают `tag:messenger`. Пример доступов лежит в `deploy/tailscale/acl.hujson`. Его дописывают в уже существующую политику tailnet, а не подменяют ей остальные машины. Участники открывают порты чата. SSH остаётся админу. Grafana стоит на том же 443, что и чат, и спрашивает пароль администратора.

Исходящий интернет хосту нужен. Tailscale ходит на координацию и DERP. Synapse ходит на `https://matrix.org/_matrix/push/v1/notify`. UDP 41641 желателен, но не обязателен: без него Tailscale остаётся на DERP.

## Сервер Matrix

Homeserver — Synapse `v1.163.0`, база — Postgres 17. Почему не Conduit и не Tuwunel: `docs/adr/0001-synapse.md`.

Рендер `scripts/render_config.py` собирает `deploy/rendered/homeserver.yaml` и не коммитит его. В файле пароль базы, `registration_shared_secret`, macaroon и form secret. Каталог `deploy/rendered` имеет режим `0700`, сам yaml — `0644`, чтобы процесс в контейнере мог его прочитать. Ключ подписи Synapse создаёт сам в томе `svoi_synapse-data`. Том не удаляют: без ключа подписи события этого сервера ломаются, даже при выключенной федерации.

Закрытые переключатели, которые рендер проверяет сам:

- `enable_registration: false`
- `federation_domain_whitelist: []`
- слушатель `names: [client]`. При `ENABLE_CALLS=true` рядом добавляется `names: [openid]` для проверки токена звонка. `names: [federation]` рендер не пишет
- `trusted_key_servers: []`
- `url_preview_enabled: false`
- `encryption_enabled_by_default: true`
- `allow_guest_access: false`
- `serve_client_wellknown: true`
- `public_baseurl: https://<имя>/` со слэшем на конце

`registration_shared_secret` нужен команде `register_new_matrix_user`. Это не открывает клиентский `/register`. `make admin USER=operator` создаёт первого администратора и печатает пароль один раз. `make user USER=alice` создаёт обычного человека. Пароль в репозиторий и в чат не кладут.

Комната по умолчанию — `make room ADMIN=... ADMIN_PASSWORD=... NAME=Друзья`. Пресет `private_chat`, шифрование включается настройкой сервера.

Лимиты одного узла: Synapse 1536 МБ, Postgres 512 МБ, Element Web 128 МБ. Хосту хватает 2 vCPU, 4 ГБ RAM и 40 ГБ диска. Профиль Grafana лучше включать на 8 ГБ. Ноутбук, который засыпает, хостом не является. Подойдут VPS или Mac mini, который не спит.

## Клиенты

Element Web получает `deploy/rendered/element-config.json`. Чужой homeserver вписать нельзя (`disable_custom_urls`), гостей и вход по почте нет, бренд «Свои», страна по умолчанию RU, тема тёмная, федерация комнаты по умолчанию выключена, каталог комнат пустой. Интеграции, карта и identity-сервер не подключаются.

На iPhone VPN On Demand стоит Always и для Wi-Fi, и для сотовой сети. Домашнюю сеть не исключают: MagicDNS есть только внутри tailnet, в том числе дома. Пошагово: `docs/guides/iphone.md`, `docs/guides/mac.md`, `docs/guides/android.md`.

## Пуши

Element X сам регистрирует пушер на `https://matrix.org/_matrix/push/v1/notify` с форматом `event_id_only`. Synapse шлёт туда идентификатор события. В сигнале нет текста. Приложение после пробуждения само читает событие с нашего homeserver, поэтому туннель на телефоне должен быть поднят. Если туннель лежит, баннер может прийти без текста или вовсе общим. Время доставки пуша в SLO не входит. Свой Sygnal не ставим: ключ APNs принадлежит приложению из магазина. Подробнее: `docs/adr/0003-push.md`.

## Звонки

По умолчанию `ENABLE_CALLS=false`. Текст, файлы и голосовые сообщения от этого не зависят.

`ENABLE_CALLS=true` добавляет LiveKit, сервис JWT и Caddy на loopback. `node_ip` LiveKit — IPv4 Tailscale из `secrets/tailscale_ipv4` или `TAILSCALE_IPV4`. `use_external_ip: false`. UDP `50000-50100` и `7881` публикуются на хосте и отсекаются от публичной сети файрволом. Кандидат ICE не должен быть публичным адресом VPS.

Проверка: два телефона, Wi-Fi выключен, сотовая сеть и Tailscale включены, сначала текст, потом звонок, потом снова текст. Если Synapse отвергает блок `matrix_rtc`, звонки выключают и оставляют чат. Инструкция: `docs/guides/calls.md`.

## Наблюдаемость, бэкап, поставка

SLO, алерты и бэкап описаны отдельно:

- `docs/sre/slo.md`
- `docs/sre/observability.md`
- `docs/sre/backup.md`
- `docs/security/threat-model.md`

Поставка — Docker Compose. На узле sui проект называется `chat`, каталог `/home/sui/sui-host/chat`. Имя проекта задаёт `deploy/compose.platform-net.yml`.

CI не деплоит. Он гоняет рендер, shellcheck, yamllint, `docker compose config` и gitleaks.

Обновление образа: бэкап, смена тега в `.env`, `make render`, `make up`, `make smoke`. Откат — предыдущий тег. Renovate предлагает теги, выкладку делает человек.

## Новый контейнер

Часть чата добавляют сервисом Compose проекта `svoi`, в файл чата или звонков. Другой продукт на ту же приставку кладут своим Compose и подключают к сети `sui`. Правила меток, по которым появляются проверки, метрики и алерты, описаны в `docs/new-service.md`. Образ с тегом из `.env.example`. У долгого сервиса одни и те же настройки: `restart: unless-stopped`, `no-new-privileges`, журнал `json-file` по 10 МБ на 5 файлов, предел памяти, секрет файлом, порт только на `127.0.0.1`. Медиа звонков вешают на адрес Tailscale. База остаётся в сети `data` без выхода в интернет.

cAdvisor и Alloy смотрят на сокет Docker всей машины, поэтому память, CPU и журнал нового контейнера попадают в Grafana и Loki без отдельной панели. Ночной Telegram от одной только новой проверки не расширяется. Будят из-за чата, диска, бэкапа, нехватки памяти, подмены адреса, неготовых звонков и тех сервисов, у которых на контейнере стоит `sui.alert=page`. Подробности для нейронки на живой машине: `docs/spec/AGENT.md`.

## Приёмка

Автоматически, командой `make ci` на примере имени:

- в отрендеренном yaml есть пустой `federation_domain_whitelist`, закрытая регистрация, выключенные превью и шифрование по умолчанию;
- слушателя федерации нет;
- `docker compose config` принимает основной файл, файл звонков и файл наблюдаемости;
- shellcheck и yamllint молчат.

На живом хосте, командой `make smoke`:

- `http://127.0.0.1:8008/health` и `/_matrix/client/versions` отвечают;
- Element Web на `127.0.0.1:8080` отвечает;
- `POST /_matrix/client/v3/register` не возвращает 200;
- порт Synapse привязан к `127.0.0.1`;
- в `compose ps` нет порта 8448;
- `/.well-known/matrix/client` содержит `m.homeserver`.

Вручную перед тем, как звать друзей:

- с телефона вне домашнего Wi-Fi открывается Element X и уходит сообщение;
- при Always-VPN баннер на iPhone приходит, пока туннель поднят;
- `make backup` и `make drill` проходят;
- чужой аккаунт tailnet не открывает SSH. Страница Grafana на 443 спрашивает пароль администратора.

## Команды, которые спецификация считает контрактом

```text
cp .env.example .env
make init
make render
make up
make serve
make smoke
make admin USER=operator
make user USER=alice
make room ADMIN=operator ADMIN_PASSWORD=... NAME=Друзья
make backup
make drill
make restic-init
make restore CONFIRM=yes SNAPSHOT=backups/<stamp>
make pin
make observe
```

`make down` останавливает основной стек, звонки и Grafana, даже если их поднимали отдельной командой. Тома он не удаляет.
