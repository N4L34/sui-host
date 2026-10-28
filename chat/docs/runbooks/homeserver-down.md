# Homeserver не отвечает

## Симптомы

Element не отправляет сообщения. `make smoke` печатает `FAIL`. Запрос к `/health` не возвращает `ok`. Контейнер `synapse` или `postgres` уходит в перезапуск.

## Вероятные причины

- Стек не запущен, или Docker на хосте остановлен.
- Postgres не становится здоровым, и Synapse из-за этого не остаётся запущенным.
- Synapse крутится в цикле: нет `deploy/rendered/homeserver.yaml`, секрет не совпадает с базой, конфиг собран с ошибкой.
- Диск заполнен. Это отдельный случай, его чинят по `docs/runbooks/disk-full.md`.

## Проверки

Выполните на хосте чата, в каталоге репозитория:

```bash
docker compose -f deploy/compose.yml --env-file .env ps
docker compose -f deploy/compose.yml --env-file .env logs --tail=200 synapse
docker compose -f deploy/compose.yml --env-file .env logs --tail=200 postgres
curl -fsS http://127.0.0.1:8008/health
curl -fsS http://127.0.0.1:8008/_matrix/client/versions
df -h
make smoke
```

`make ps` показывает тот же стек, что и `make up`, включая звонки, если они включены. Команда `docker compose` выше показывает основной стек.

## Исправление

1. Если `df -h` показывает 100%, остановитесь и откройте `docs/runbooks/disk-full.md`. Тома Docker не удаляйте.
2. Если Docker не запущен, запустите его. На VPS это `systemctl start docker`. На Mac откройте Docker Desktop или OrbStack.
3. Поднимите стек ещё раз:

```bash
make up
```

`make up` запускает Synapse только после того, как Postgres станет здоровым. Том базы при этом не создаётся заново.

4. Если в логе Synapse нет файла конфигурации или вы меняли `.env`, соберите конфиг заново и поднимите стек:

```bash
make render
make up
make serve
```

5. Если Postgres не проходит проверку здоровья, читайте его лог. Не удаляйте том `svoi_postgres-data` и не запускайте пустой кластер. Если данные испорчены, восстановите снимок по `docs/runbooks/restore.md`.
6. Если `curl` к `127.0.0.1:8008` проходит, а друзья подключиться не могут, сервер жив, а tailnet нет. Откройте `docs/runbooks/tailscale-down.md`.

Не выполняйте `docker compose down -v` и `docker volume prune`. Так вы сотрёте базу и ключ подписи.

## Как убедиться

Обе команды заканчиваются без ошибки:

```bash
curl -fsS http://127.0.0.1:8008/health
make smoke
```

`docker compose -f deploy/compose.yml --env-file .env ps` показывает `synapse` и `postgres` как запущенные, без цикла перезапуска. Отправьте сообщение от operator к alice и дождитесь его на втором устройстве.
