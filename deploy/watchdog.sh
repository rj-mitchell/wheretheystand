#!/usr/bin/env bash
# Restart the site if it has stopped answering. Docker checks /api/health every 30 seconds and marks the app
# "unhealthy" after three failures in a row, but does not restart it by itself; hansard-watchdog.timer runs this
# every two minutes to do so. Each restart is written to the system log: journalctl -t hansard-watchdog
set -uo pipefail
cd "${DIR:-/opt/hansardgpt}/deploy"
id=$(docker compose ps -q app 2>/dev/null)
[[ -n "$id" ]] || exit 0  # not running (being rebuilt, say): docker compose and its restart policy look after that
status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null)
if [[ "$status" == "unhealthy" ]]; then
  logger -t hansard-watchdog "the site stopped answering its health check; restarting it"
  docker compose restart app
fi
