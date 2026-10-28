# sui

Приставка `sui.example.com`, адрес `100.64.0.10`, каталог чата `/home/sui/tailnet-messenger`. Этот репозиторий описывает машину. Код чата живёт в `N4L34/tailnet-messenger`.

Пока не прочитаны `docs/work.md` и `docs/new-service.md`, на приставке не выполняют `systemctl restart svoi.service`, `make down`, `docker compose down`, пересоздание `svoi-livekit-1` и полный `scripts/render_config.py`.

Секреты, пароли и токены в git, в ответы и в память не копируют. Новый процесс заводят контейнером проекта `svoi` либо отдельным проектом Compose по правилам `docs/new-service.md`. `docker run` мимо Compose не оставляют.
