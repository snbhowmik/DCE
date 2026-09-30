#!/usr/bin/env bash
# One-shot installer for RHEL 8.10 (also Rocky/Alma 8). Run as root from the repo checkout:
#   sudo bash deploy/install.sh            # installs to /opt/dce, serves on port 80
# Idempotent: re-run it after pulling new code. Data drops must already be in data/incoming/.
set -euo pipefail
APP=/opt/dce
SRC="$(cd "$(dirname "$0")/.." && pwd)"
USER_=dce
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "System packages (nginx, Node 20, build basics)"
dnf -y install nginx httpd-tools git tar rsync gcc gcc-c++ make libgomp policycoreutils-python-utils
dnf -y module reset nodejs && dnf -y module enable nodejs:20 && dnf -y install nodejs
corepack enable
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

say "App user and files → $APP"
id -u "$USER_" >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/dce --shell /sbin/nologin "$USER_"
mkdir -p "$APP"
rsync -a --delete --exclude .venv --exclude 'frontend/node_modules' --exclude 'frontend/.next' \
  --exclude 'data/processed' --exclude .env "$SRC"/ "$APP"/
mkdir -p "$APP/data/processed"
[ -f "$APP/.env" ] || { [ -f "$SRC/.env" ] && cp "$SRC/.env" "$APP/.env" || cp "$APP/.env.example" "$APP/.env"; }
chown -R "$USER_:$USER_" "$APP" && chmod 600 "$APP/.env"

run() { sudo -u "$USER_" -H env HOME=/var/lib/dce UV_PYTHON_INSTALL_DIR=/var/lib/dce/python \
  UV_CACHE_DIR=/var/lib/dce/.cache/uv PATH=/usr/local/bin:/usr/bin:/bin bash -lc "cd $APP && $*"; }

say "Python env (uv installs its own CPython 3.12; system Python untouched)"
run "uv python install 3.12 && uv sync --all-packages --frozen --python 3.12"

say "Frontend build"
run "cd frontend && COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm install --frozen-lockfile && pnpm build"

say "Ingest data drops + precompute every world × strategy (a few minutes)"
run "bash deploy/refresh-worlds.sh --no-restart"

say "systemd services"
install -m 644 "$APP/deploy/dce-api.service" /etc/systemd/system/dce-api.service
install -m 644 "$APP/deploy/dce-web.service" /etc/systemd/system/dce-web.service
systemctl daemon-reload
systemctl enable --now dce-api dce-web
systemctl restart dce-api dce-web

say "nginx on :80 (SELinux + firewall)"
install -m 644 "$APP/deploy/nginx-dce.conf" /etc/nginx/conf.d/dce.conf
[ -f /etc/nginx/dce.htpasswd ] || touch /etc/nginx/dce.htpasswd
setsebool -P httpd_can_network_connect 1
nginx -t && systemctl enable --now nginx && systemctl reload nginx
if systemctl is-active --quiet firewalld; then firewall-cmd --permanent --add-service=http && firewall-cmd --reload; fi

say "Smoke test"
sleep 3
curl -fsS http://127.0.0.1/api/v1/health && echo
curl -fsS -o /dev/null -w "dashboard HTTP %{http_code}\n" http://127.0.0.1/
echo "Open http://$(hostname -I | awk '{print $1}')/  ·  logs: journalctl -u dce-api -u dce-web -f"
