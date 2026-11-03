# Свои

Чат — папка `chat` в репозитории. Имя сервера задаётся локально. Общий закон узла: `../AGENTS.md`. Памятка по чату: `docs/spec/AGENT.md`.

`systemctl restart svoi.service` и `systemctl stop svoi.service` останавливают контейнеры чата. `make up` запускает `render_config.py` и на узле его не выполняют. `docker compose down` не вызывают. Секреты в ответы, git и память не копируют.
