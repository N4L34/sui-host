# sui

Приставка `sui.example.com`, адрес `100.64.0.10`. Этот репозиторий — платформа машины: метрики, журнал, алерты и правила нового сервиса. Код чата живёт в `N4L34/tailnet-messenger`, каталог на машине `/home/sui/tailnet-messenger`. Платформа на машине: `/home/sui/sui-host`.

Пока не прочитаны `docs/work.md` и `docs/new-service.md`, на приставке не выполняют `systemctl restart svoi.service`, `make down`, `docker compose down`, пересоздание `svoi-livekit-1` и полный `scripts/render_config.py`.

Секреты, пароли и токены в git, в ответы и в память не копируют. Новый процесс заводят своим проектом Compose по `docs/new-service.md` и подключают к сети `sui`. В проект чата его не вкладывают. `docker run` мимо Compose не оставляют.

`sui-platform.service` останавливают через `docker compose stop` в `/home/sui/sui-host`. `down` тома с историей графиков не удаляет, но контейнеры снимает, поэтому в юните его нет.
