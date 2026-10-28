# Эталон

Способ запуска решает `AGENTS.md`. Здесь набор, которым репозиторий и узел проверяются.

CI один, на раннере GitHub, и на приставку не заходит. CD один: локальный раннер обновляет клон `/home/sui/sui-host` и в изменившейся папке сервиса выполняет `docker compose up -d`. Папка с одними документами контейнеры не трогает. Папка `chat` поднимается своим набором файлов Compose и не вызывает `make up`. В `main` попадают изменения с зелёным CI.

## Срез на каждый Compose

Один Prometheus и одна Grafana. У каждого проекта Compose свой срез.

Имя берётся из `monitoring.project`. Если метки нет, `discover.py` берёт `com.docker.compose.project`. Цель пишется как `проект/сервис`. Два проекта с сервисом `api` не сливаются. Дашборд `Свои` фильтрует контейнеры и проверки этим именем. Telegram пишет имя проекта перед текстом. Alertmanager группирует по `alertname` и `project`, когда на узле есть `runtime/telegram_chat_id` и деплой подставляет шаблон.

Чат — проект `chat`. Платформа — проект `monitoring`. Новый продукт называется своим именем Compose.

## CI

Команда одна: `scripts/ci.sh`.

Она гоняет самотест discover, сверку тегов `.env.example` с `docs/services.md`, `docker compose config`, `scripts/policy.py`, `promtool check config`, `promtool test rules`, проверку Loki и Alloy, `amtool check-config` на шаблоне, yamllint, shellcheck, actionlint и gitleaks.

Политика режет `latest`, порт не на `127.0.0.1`, отсутствие лимита памяти и `memswap_limit`, чужой журнал, `privileged` не у cAdvisor, сокет Docker не у Alloy и сумму лимитов выше 8 ГиБ.

Renovate открывает pull request на новый тег образа, ждёт три дня и сам не сливает.

## CD

Контейнер раннера описан в `runner/compose.yml`. Токен лежит в `runner/runner_token` на узле и в git не входит. Раннер зарегистрирован на `N4L34/sui-host` с меткой `sui`.

`deploy` стартует после успешного `ci` на `main`. Одно задание за раз. Коммит только из документации контейнеры не трогает. Диск, занятый на 85 % и больше, деплой не начинает.

Команда на узле для изменившейся папки: `docker compose up -d` в `/home/sui/sui-host/<папка>`. `down` нет. Если менялся мониторинг, с приставки открываются Grafana, Prometheus и `/_matrix/client/versions`. Не сошлось — возвращаются предыдущие файлы этой папки и тот же `compose up -d`. SHA пишется в textfile node exporter, если каталог на месте.

Почему раннер локальный, а не вход через Tailscale с раннера GitHub: `docs/adr/0001-local-runner.md`.
