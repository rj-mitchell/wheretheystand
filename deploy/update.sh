#!/usr/bin/env bash
# Install the latest version of Where They Stand (whatever has been merged into `main` on GitHub).
# Run on the server, as root:
#   bash /opt/hansardgpt/deploy/update.sh
# It fetches the new code, rebuilds and restarts the site (a few seconds of downtime), refreshes the timers,
# clears out old build files, and finishes with the health check.
set -euo pipefail
DIR="${DIR:-/opt/hansardgpt}"

echo "==> Fetching the latest version"
caddy_before="${CADDY_BEFORE:-$(sha1sum "$DIR/deploy/Caddyfile" | cut -d' ' -f1)}"
self_before=$(sha1sum "$DIR/deploy/update.sh" | cut -d' ' -f1)
git -C "$DIR" pull --ff-only
# bash goes on reading the copy of this script it started with, so a new version only takes effect if run afresh
if [[ -z "${UPDATE_RESTARTED:-}" && "$(sha1sum "$DIR/deploy/update.sh" | cut -d' ' -f1)" != "$self_before" ]]; then
  echo "==> This update script has changed: running the new version"
  CADDY_BEFORE="$caddy_before" UPDATE_RESTARTED=1 exec bash "$DIR/deploy/update.sh"
fi

cd "$DIR/deploy"
echo "==> Refreshing the update and backup timers"
cp hansard-sync.service hansard-sync.timer hansard-backup.service hansard-backup.timer \
   hansard-watchdog.service hansard-watchdog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hansard-sync.timer hansard-backup.timer hansard-watchdog.timer >/dev/null

echo "==> Making sure the server has swap space (spare memory on disk for large updates)"
bash "$DIR/deploy/swap.sh" || echo "WARNING: could not add swap; carrying on."

echo "==> Refreshing the web server's own settings (Cloudflare's addresses, the certificate)"
local_before=$(cat local/*.caddy 2>/dev/null | sha1sum | cut -d' ' -f1)
bash "$DIR/deploy/caddy-local.sh"
local_after=$(cat local/*.caddy | sha1sum | cut -d' ' -f1)

echo "==> Rebuilding and restarting the site"
docker compose up -d --build
if [[ "$(sha1sum Caddyfile | cut -d' ' -f1)" != "$caddy_before" || "$local_after" != "$local_before" ]]; then
  echo "==> The web server's settings changed: restarting it"
  docker compose up -d --force-recreate --no-deps caddy  # a file mounted into the container is re-read only on a fresh start
fi

echo "==> Clearing out old build files"
docker image prune -f >/dev/null

echo "==> Checking everything is working"
sleep 10
bash "$DIR/deploy/check.sh"
