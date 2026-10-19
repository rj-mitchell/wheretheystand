#!/usr/bin/env bash
# Check that Where They Stand is set up and working. Run on the server, as root:
#   bash /opt/hansardgpt/deploy/check.sh
# It changes nothing. Each line is a tick (fine), a cross (needs attention, with what to do) or a note.
set -uo pipefail
# Never pipe a long output into "grep -q": grep stops at the first match, the writer is cut off, and pipefail then
# reports a match as a failure. Match against "$(command)" instead.

DIR="${DIR:-/opt/hansardgpt}"
cd "$DIR/deploy" 2>/dev/null || { echo "✗ $DIR/deploy not found: run the setup script first (step 4 of the guide)."; exit 1; }

problems=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n      → %s\n' "$1" "$2"; problems=$((problems + 1)); }
note() { printf '  • %s\n' "$1"; }
env_value() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }

echo "Where They Stand: checking the server"
echo

echo "Settings"
if [[ -f .env ]]; then
  ok "settings file found (deploy/.env)"
else
  bad "no settings file (deploy/.env)" "run the setup script: bash provision.sh"
fi
DOMAIN="$(env_value DOMAIN)"
REDIRECTS="$(env_value REDIRECT_DOMAINS)"
[[ -n "$DOMAIN" ]] && ok "main address: $DOMAIN" || bad "no DOMAIN set" "add DOMAIN=wheretheystand.uk to deploy/.env"
if [[ -n "$(env_value HANSARD_ANTHROPIC_API_KEY)" || -n "$(env_value ANTHROPIC_API_KEY)" ]]; then
  ok "Anthropic API key is set (not shown)"
else
  bad "no Anthropic API key: the site will show sources but not write briefings" \
      "add HANSARD_ANTHROPIC_API_KEY=… to deploy/.env, then: docker compose up -d"
fi
[[ -n "$(env_value HANSARD_ADMIN_TOKEN)" ]] && ok "admin token is set" || bad "no admin token" "add HANSARD_ADMIN_TOKEN=\$(openssl rand -hex 24) to deploy/.env"
echo

echo "Site"
running="$(docker compose ps --status running --services 2>/dev/null)"
for svc in app caddy; do
  if grep -qx "$svc" <<<"$running"; then
    ok "$svc is running"
  else
    bad "$svc is not running" "cd $DIR/deploy && docker compose up -d   (then docker compose logs $svc to see why)"
  fi
done
key_result="$(docker compose exec -T app python -c "
import anthropic
from hansard.rag.answer import api_key, make_client
if not api_key():
    print('none')
else:
    try:
        make_client().models.list(limit=1)
        print('ok')
    except anthropic.AuthenticationError:
        print('rejected')
    except Exception as e:
        print('unreachable ' + type(e).__name__)
" 2>/dev/null | tail -n 1)"
case "$key_result" in
  ok) ok "Anthropic accepts the API key (briefings can be written)";;
  none) ;;  # already reported above
  rejected) bad "Anthropic rejected the API key: briefings will fail" \
      "put in a working key: see 'Change the API key' in the guide's Day to day table";;
  *) bad "could not check the API key with Anthropic (${key_result:-no answer})" "try again in a minute; if it persists, docker compose logs --tail 50 app";;
esac
if docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=10)" >/dev/null 2>&1; then
  ok "the app answers inside the server"
else
  bad "the app does not answer" "docker compose logs --tail 50 app"
fi
if [[ -n "$DOMAIN" ]]; then
  proxied=""
  grep -qi '^server: cloudflare' <<<"$(curl -sI --max-time 20 "https://$DOMAIN/" 2>/dev/null)" && proxied=1
  if [[ -f "$DIR/deploy/local/behind-cloudflare" ]]; then
    if [[ -n "$proxied" ]]; then
      ok "$DOMAIN goes through Cloudflare's proxy, with Cloudflare's certificate on this server"
    else
      bad "$DOMAIN is set up for Cloudflare's proxy, but is not going through it, so visitors cannot reach it" \
          "in Cloudflare's DNS settings, set the $DOMAIN A record to 'Proxied' (orange cloud), or run: bash $DIR/deploy/cloudflare.sh off"
    fi
    grep -q 'remote_ip' "$DIR/deploy/local/site.caddy" 2>/dev/null \
      || bad "Cloudflare's certificate is not in use (Cloudflare's addresses could not be fetched)" \
             "check this server can reach www.cloudflare.com, then run: bash $DIR/deploy/cloudflare.sh on"
  elif [[ -n "$proxied" ]]; then
    bad "$DOMAIN is going through Cloudflare's proxy (orange cloud), but this server is not set up for it" \
        "run: bash $DIR/deploy/cloudflare.sh on (see 'Cloudflare' in the guide), or set the A record back to 'DNS only' (grey cloud)"
  fi
  grep -q 'trusted_proxies' "$DIR/deploy/local/proxies.caddy" 2>/dev/null \
    || note "Cloudflare's list of addresses has not been fetched yet (needed only behind Cloudflare's proxy; update.sh tries again)"
  status="$(curl -fsS --max-time 20 "https://$DOMAIN/api/status" 2>/dev/null)"
  if [[ -n "$status" ]]; then
    ok "https://$DOMAIN works, with a valid certificate"
    python3 - "$status" <<'PY' 2>/dev/null
import json, sys
s = json.loads(sys.argv[1])
print(f"  • briefings: {s.get('briefings')}; most recent record: {s.get('latest_record') or 'not loaded yet'}; "
      f"meaning-based search covers {round(100 * (s.get('meaning_search_coverage') or 0))}% so far")
PY
  else
    bad "https://$DOMAIN does not work yet" \
        "check nslookup $DOMAIN shows this server's IP ($(hostname -I 2>/dev/null | awk '{print $1}')); certificates are issued once it does (docker compose logs caddy)"
  fi
  IFS=',' read -ra names <<<"$REDIRECTS"
  for n in "${names[@]}"; do
    n="$(echo "$n" | xargs)"
    [[ -z "$n" ]] && continue
    result="$(curl -s -o /dev/null --max-time 20 -w '%{http_code} %{redirect_url}' "https://$n/" 2>/dev/null)"
    if [[ "$result" =~ ^30[178]\ https://$DOMAIN/ ]]; then
      ok "$n redirects to $DOMAIN"
    else
      if [[ -f "$DIR/deploy/local/redirects-at-cloudflare" ]]; then
        bad "$n does not redirect (got: ${result:-no answer})" \
            "Cloudflare redirects it: check its DNS record is Proxied (orange) and its redirect rule (see 'Cloudflare' in the guide)"
      else
        bad "$n does not redirect yet (got: ${result:-no answer})" "check its DNS A record points at this server; certificates follow automatically"
      fi
    fi
  done
fi
echo

echo "Data"
if grep -q "bootstrap" <<<"$(docker ps --format '{{.Command}}' 2>/dev/null)"; then
  note "the first data load is still running: $(tail -n 1 /var/log/hansard-bootstrap.log 2>/dev/null | cut -c1-120)"
elif [[ -f /var/log/hansard-bootstrap.log ]]; then
  if grep -q '"next"' /var/log/hansard-bootstrap.log; then
    ok "the first data load finished"
  else
    bad "the first data load stopped before finishing" "see the end of: tail -n 40 /var/log/hansard-bootstrap.log"
  fi
fi
echo

echo "Updates, backups and security"
if [[ -n "$(swapon --show --noheadings 2>/dev/null)" ]]; then
  ok "swap space is on (memory: $(free -h | awk '/^Mem:/ {print $2}'), swap: $(free -h | awk '/^Swap:/ {print $2}'))"
else
  note "no swap space: a large update can run out of memory (to add it: bash $DIR/deploy/swap.sh)"
fi
for t in hansard-sync.timer hansard-backup.timer; do
  if systemctl is-enabled --quiet "$t" 2>/dev/null; then
    ok "$t is on (next: $(systemctl show "$t" -p NextElapseUSecRealtime --value 2>/dev/null))"
  else
    bad "$t is not on" "systemctl enable --now $t"
  fi
done
last_backup="$(ls -1t backups/where-they-stand-*.tar.gz 2>/dev/null | head -1)"
[[ -n "$last_backup" ]] && ok "latest backup: $last_backup" || note "no backups yet (the first runs at 3.30am)"
grep -q "Status: active" <<<"$(ufw status 2>/dev/null)" && ok "firewall is on" || bad "firewall is off" "ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable"
systemctl is-active --quiet fail2ban 2>/dev/null && ok "fail2ban is on" || bad "fail2ban is off" "apt-get install -y fail2ban && systemctl enable --now fail2ban"
# what matters is apt's daily setting (the last one read wins) and the timer that acts on it
AUTO_FIX="printf 'APT::Periodic::Update-Package-Lists \"1\";\\nAPT::Periodic::Unattended-Upgrade \"1\";\\n' > /etc/apt/apt.conf.d/99zz-auto-upgrades && systemctl unmask apt-daily.timer apt-daily-upgrade.timer && systemctl enable --now apt-daily.timer apt-daily-upgrade.timer"
setting="$(apt-config dump 2>/dev/null | grep '^APT::Periodic::Unattended-Upgrade ' | tail -1)"
timer="$(systemctl is-enabled apt-daily-upgrade.timer 2>/dev/null || true)"
if [[ "$setting" == *'"1"'* && "$timer" == enabled ]]; then
  ok "automatic security updates are on"
elif [[ "$setting" != *'"1"'* ]]; then
  bad "automatic security updates are off (apt setting: ${setting:-not set})" "$AUTO_FIX"
else
  bad "automatic security updates are set up, but their daily timer is ${timer:-missing}" "$AUTO_FIX"
fi
if grep -qi "^passwordauthentication no" <<<"$(sshd -T 2>/dev/null)"; then
  ok "password logins are off (key only)"
else
  note "password logins are still on: fine for now; step 5 of the guide switches to a key"
fi
free_pct="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
[[ "${free_pct:-0}" -lt 85 ]] && ok "disk ${free_pct}% used" || bad "disk ${free_pct}% used" "ask for help freeing space before it fills"
echo

if [[ $problems -eq 0 ]]; then
  echo "All good."
else
  echo "$problems thing(s) need attention (marked ✗ above)."
fi
exit $(( problems > 0 ))
