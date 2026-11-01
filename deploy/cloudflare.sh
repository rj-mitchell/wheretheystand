#!/usr/bin/env bash
# Put the main address behind Cloudflare's proxy (the orange cloud), or take it out again. Run on the server, as root:
#   bash /opt/hansardgpt/deploy/cloudflare.sh on    after turning the orange cloud on (asks for the origin certificate)
#   bash /opt/hansardgpt/deploy/cloudflare.sh off   before turning the orange cloud off
#   bash /opt/hansardgpt/deploy/cloudflare.sh redirects on    once Cloudflare redirects www and the other domain
#                                                             names itself: checks each, then stops the server doing it
#   bash /opt/hansardgpt/deploy/cloudflare.sh redirects off   has the server redirect them again
#   bash /opt/hansardgpt/deploy/cloudflare.sh site DOMAIN     one of the charity's other sites (SITES in .env): saves
#                                                             its origin certificate and serves it
# "on" saves Cloudflare's origin certificate in deploy/local/ and has Caddy use it, and closes any connection that
# does not come through Cloudflare. If the site does not then answer through Cloudflare, it puts everything back.
# Add --replace to "on" to paste a new certificate instead of using the saved one. See "Cloudflare" in docs/HOSTING.md.
set -euo pipefail
DIR="${DIR:-/opt/hansardgpt}"
LOCAL="$DIR/deploy/local"
cd "$DIR/deploy"
DOMAIN="$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2- | tr -d '"')"
REDIRECTS="$(grep -E '^REDIRECT_DOMAINS=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr ',' ' ')"
[[ -n "$DOMAIN" ]] || { echo "No DOMAIN in $DIR/deploy/.env: run the setup script first."; exit 1; }
mkdir -p "$LOCAL"

restart_caddy() {
  bash "$DIR/deploy/caddy-local.sh"
  docker compose up -d --force-recreate --no-deps caddy >/dev/null  # a mounted file is re-read only on a fresh start
  sleep 5
}
through_cloudflare() { grep -qi '^server: cloudflare' <<<"$(curl -sI --max-time 20 "https://$DOMAIN/" 2>/dev/null)"; }

# Read a pasted certificate or key, up to and including its "-----END ...-----" line
read_pem() {
  local line out=""
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ -z "$out" && -z "${line// /}" ]] && continue
    out+="$line"$'\n'
    [[ "$line" == -----END*----- ]] && break
  done
  printf '%s' "$out"
}

# Ask for an origin certificate and its private key for one domain, check them, and save them (as $2 and $3)
take_certificate() {
  local domain="$1" pem="$2" key="$3" cert pkey tmp
  echo "Paste the origin certificate (from -----BEGIN CERTIFICATE----- to -----END CERTIFICATE-----), then press Return:"
  cert="$(read_pem)"
  echo "Now paste the private key (from -----BEGIN PRIVATE KEY----- to -----END PRIVATE KEY-----), then press Return:"
  pkey="$(read_pem)"
  tmp="$(mktemp -d)"
  # removed however the script ends (the name is fixed now: the variable is gone once this function returns)
  trap "rm -rf -- '$tmp'" EXIT
  printf '%s' "$cert" > "$tmp/origin.pem"
  (umask 077; printf '%s' "$pkey" > "$tmp/origin.key")
  openssl x509 -in "$tmp/origin.pem" -noout 2>/dev/null \
    || { echo "That certificate could not be read: nothing has been changed. Run this again and paste it whole."; exit 1; }
  openssl pkey -in "$tmp/origin.key" -noout 2>/dev/null \
    || { echo "That private key could not be read: nothing has been changed. Run this again and paste it whole."; exit 1; }
  [[ "$(openssl x509 -in "$tmp/origin.pem" -pubkey -noout | sha256sum)" == \
     "$(openssl pkey -in "$tmp/origin.key" -pubout | sha256sum)" ]] \
    || { echo "The private key does not belong to that certificate: nothing has been changed."; exit 1; }
  openssl x509 -in "$tmp/origin.pem" -noout -checkhost "$domain" | grep -q 'does match' \
    || { echo "That certificate is not for $domain: nothing has been changed. Create one that lists $domain."; exit 1; }
  mkdir -p "$(dirname "$pem")"
  install -m 600 "$tmp/origin.pem" "$pem"
  install -m 600 "$tmp/origin.key" "$key"
  echo "Saved: the certificate is valid until $(openssl x509 -in "$pem" -noout -enddate | cut -d= -f2)."
}

case "${1:-}" in
  on)
    if ! through_cloudflare; then
      cat <<MSG
https://$DOMAIN is not going through Cloudflare yet, so nothing has been changed.
In Cloudflare: set SSL/TLS to "Full (strict)", then set the $DOMAIN A record (name @) to "Proxied" (orange cloud).
Wait a few minutes and run this again. (Visitors can still reach the site meanwhile.)
MSG
      exit 1
    fi
    if [[ "${2:-}" == "--replace" || ! -f "$LOCAL/origin.pem" || ! -f "$LOCAL/origin.key" ]]; then
      take_certificate "$DOMAIN" "$LOCAL/origin.pem" "$LOCAL/origin.key"
    else
      echo "Using the origin certificate saved earlier (add --replace to paste a new one)."
    fi
    touch "$LOCAL/behind-cloudflare"
    echo "==> Restarting the web server with Cloudflare's certificate"
    restart_caddy
    if ! grep -q 'remote_ip' "$LOCAL/site.caddy"; then
      rm -f "$LOCAL/behind-cloudflare"
      restart_caddy
      echo "Cloudflare's list of addresses could not be fetched from www.cloudflare.com, so nothing has been changed. Try again later."
      exit 1
    fi
    if curl -fsS --max-time 20 -o /dev/null "https://$DOMAIN/api/health" && through_cloudflare; then
      echo "✓ https://$DOMAIN answers through Cloudflare, using Cloudflare's certificate on this server."
      if curl -sk --max-time 10 -o /dev/null --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/"; then
        echo "✗ but a connection that bypasses Cloudflare was answered too: please report this."
      else
        echo "✓ Connections that bypass Cloudflare are closed."
      fi
      echo "Now run: bash $DIR/deploy/check.sh"
    else
      rm -f "$LOCAL/behind-cloudflare"
      restart_caddy
      cat <<MSG
✗ https://$DOMAIN did not answer through Cloudflare with the new settings, so they have been undone and the site is
  as it was. Check that SSL/TLS in Cloudflare is set to "Full (strict)" and that the certificate was created for
  $DOMAIN, then run this again (with --replace to paste a different certificate).
MSG
      exit 1
    fi
    ;;
  off)
    rm -f "$LOCAL/behind-cloudflare"
    echo "==> Restarting the web server with its own certificate (from Let's Encrypt)"
    restart_caddy
    cat <<MSG
✓ The server now accepts visitors directly again. In Cloudflare, you can set the $DOMAIN A record back to
  "DNS only" (grey cloud). The saved origin certificate is kept, so "cloudflare.sh on" switches back without it.
MSG
    ;;
  redirects)
    if [[ "${2:-}" == "off" ]]; then
      rm -f "$LOCAL/redirects-at-cloudflare"
      echo "==> Restarting the web server, which redirects $REDIRECTS again"
      restart_caddy
      echo "✓ Done. Point each of those names' A records at this server (DNS only) and remove Cloudflare's redirect"
      echo "  rules; the server gets their certificates within a few minutes of the DNS change."
      exit 0
    fi
    [[ "${2:-}" == "on" ]] || { echo "Usage: bash $DIR/deploy/cloudflare.sh redirects on|off"; exit 1; }
    failed=0
    for n in $REDIRECTS; do
      head="$(curl -sI --max-time 20 "https://$n/redirect-check?from=$n" 2>/dev/null | tr -d '\r' || true)"
      location="$(grep -i '^location:' <<<"$head" | cut -d' ' -f2- || true)"
      if [[ -z "$head" ]]; then
        echo "✗ $n does not answer yet (Cloudflare can take a little while to issue its certificate for a new domain)"
      elif ! grep -qi '^server: cloudflare' <<<"$head"; then
        echo "✗ $n is not going through Cloudflare: set its DNS record to Proxied (orange cloud)"
      elif grep -qi '^x-redirect-source: server' <<<"$head"; then
        echo "✗ $n is still being redirected by this server, not by Cloudflare: add Cloudflare's redirect rule for it"
      elif [[ "$location" != "https://$DOMAIN/redirect-check?from=$n" ]]; then
        echo "✗ $n is not redirected as expected (sent to: ${location:-nowhere}): it should go to https://$DOMAIN with the"
        echo "  rest of the address kept; check the redirect rule"
      else
        echo "✓ $n: Cloudflare redirects it to $DOMAIN"
        continue
      fi
      failed=1
    done
    if (( failed )); then
      echo "Nothing has been changed: the server still redirects these names. Fix the ones marked ✗ and run this again."
      exit 1
    fi
    touch "$LOCAL/redirects-at-cloudflare"
    echo "==> Restarting the web server, which now leaves these names to Cloudflare"
    restart_caddy
    echo "✓ Done. Now run: bash $DIR/deploy/check.sh"
    ;;
  site)
    # one of the charity's other sites on this server (SITES in .env): save its origin certificate and serve it
    site="${2:-}"
    SITES="$(grep -E '^SITES=' .env | head -1 | cut -d= -f2- | tr -d '"' || true)"
    if [[ -z "$site" || " $SITES " != *" $site="* ]]; then
      echo "Usage: bash $DIR/deploy/cloudflare.sh site DOMAIN, for a DOMAIN listed in SITES in $DIR/deploy/.env"
      echo "(SITES is now: ${SITES:-empty})"
      exit 1
    fi
    take_certificate "$site" "$LOCAL/sites/$site.pem" "$LOCAL/sites/$site.key"
    echo "==> Restarting the web server, which now serves $site"
    restart_caddy
    if ! grep -q "^$site {" "$LOCAL/sites.caddy"; then
      echo "✗ $site is not served: Cloudflare's list of addresses could not be fetched from www.cloudflare.com. Try again later."
      exit 1
    fi
    # the site's own answer ({"ok": true}), not just any page: a "coming soon" page answers every address too
    reply="$(curl -s --max-time 20 -w '\n%{http_code}' "https://$site/api/health" || true)"
    code="${reply##*$'\n'}"
    if [[ "$code" == 200 && "$reply" == *'"ok"'* ]] && grep -qi '^server: cloudflare' <<<"$(curl -sI --max-time 20 "https://$site/" 2>/dev/null)"; then
      echo "✓ https://$site answers through Cloudflare, using its certificate on this server."
    elif [[ "$code" == 502 ]]; then
      echo "✓ The certificate is in place. https://$site answers once its site is started (its own update.sh)."
    elif [[ "$code" == 200 ]]; then
      echo "✓ The certificate is in place. https://$site still shows another page (a \"coming soon\" page, say): it"
      echo "  reaches this server once its DNS record points here."
    else
      echo "• https://$site does not answer yet (got: ${code:-nothing}). Check its DNS record (Proxied, pointing at this"
      echo "  server) and SSL/TLS \"Full (strict)\" in Cloudflare; the certificate is saved either way."
    fi
    ;;
  *)
    echo "Usage: bash $DIR/deploy/cloudflare.sh on [--replace] | off | redirects on|off | site DOMAIN"
    exit 1
    ;;
esac
