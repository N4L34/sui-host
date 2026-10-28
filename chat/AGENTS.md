# Свои

Живой чат стоит на приставке `sui`, каталог `/home/sui/tailnet-messenger`, имя сервера `sui.example.com`. Шаблон в git и эта машина расходятся. Полная памятка для нейронки и нового разработчика: `docs/spec/AGENT.md`.

Новый контейнер добавляют сервисом проекта `svoi` с теми же правилами, что у остальных: loopback, предел памяти, журнал json-file, `no-new-privileges`. Память, CPU и логи подхватывают cAdvisor и Alloy. HTTP-проверку дописывают в `blackbox-apps`. Ночной алерт заводят только если без сервиса другу ломается чат или звонок.

Пока эта памятка не прочитана, на сервере не выполняют `systemctl restart svoi.service`, `make down`, `docker compose down`, пересоздание `svoi-livekit-1` и полный `scripts/render_config.py`. Секреты в ответы, git и память не копируют.
