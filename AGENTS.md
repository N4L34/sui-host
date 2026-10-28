# sui

Узел `sui.example.com`, адрес `100.64.0.10`. Этот репозиторий держит платформу: метрики, журнал, алерты и контракт нового сервиса. Код чата лежит в `N4L34/tailnet-messenger`, на узле это `/home/sui/tailnet-messenger`. Платформа на узле: `/home/sui/sui-host`.

До чтения `docs/work.md` и `docs/new-service.md` на узле не выполняют `systemctl restart svoi.service`, `make down`, `docker compose down`, пересоздание `svoi-livekit-1` и полный `scripts/render_config.py`.

Секреты, пароли и токены не попадают в git, в ответы и в память. Новый процесс заводят отдельным проектом Compose по `docs/new-service.md` и подключают к сети `sui`. В проект чата его не добавляют. Процесс вне Compose не оставляют.

`sui-platform.service` останавливают командой `docker compose stop` в `/home/sui/sui-host`. В юните нет `down`: эта команда удаляет контейнеры.
