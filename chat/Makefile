.PHONY: init render up down serve smoke backup drill restore restic-init \
	admin user room pin ci observe logs ps

init:
	bash scripts/init.sh

render:
	python3 scripts/render_config.py

up:
	bash scripts/up.sh

down:
	bash scripts/down.sh

serve:
	bash scripts/serve.sh

smoke:
	bash scripts/smoke.sh

backup:
	bash scripts/backup.sh

drill:
	bash scripts/restore-drill.sh

restic-init:
	bash -c 'set -a; source .env; set +a; export RESTIC_PASSWORD_FILE="$$PWD/secrets/restic_password"; restic init'

restore:
	CONFIRM="$(CONFIRM)" SNAPSHOT="$(SNAPSHOT)" bash scripts/restore.sh

admin:
	AS_ADMIN=true bash scripts/new-user.sh "$(USER)"

user:
	bash scripts/new-user.sh "$(USER)"

room:
	@test -n "$(ADMIN)" && test -n "$(ADMIN_PASSWORD)" || (echo "usage: make room ADMIN=operator ADMIN_PASSWORD=... NAME=Друзья" && exit 1)
	python3 scripts/matrix_admin.py room --admin "$(ADMIN)" --password "$(ADMIN_PASSWORD)" --name "$(or $(NAME),Друзья)"

observe:
	SVOI_OBSERVE=true bash scripts/up.sh
	SVOI_OBSERVE=true bash scripts/serve.sh

logs:
	bash -c 'source scripts/lib.sh; compose logs -f --tail=100'

ps:
	bash -c 'source scripts/lib.sh; compose ps'

pin:
	bash scripts/pin-images.sh

ci:
	bash scripts/ci.sh
