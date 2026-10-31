#!/usr/bin/env bash
# This server's own settings for Caddy, in deploy/local/ (not in git): Cloudflare's proxy addresses, and the
# certificate settings. Run by update.sh and provision.sh before Caddy starts; safe to run again at any time.
#
#   proxies.caddy  Cloudflare's addresses, fetched from cloudflare.com, so that a request coming through Cloudflare
#                  is credited to the visitor (CF-Connecting-IP). Harmless when the site is not behind Cloudflare:
#                  only requests from Cloudflare's own addresses are trusted. Kept as it was if the fetch fails.
#   site.caddy     Empty (Caddy gets its certificate from Let's Encrypt) unless deploy/cloudflare.sh has switched
#                  the site to Cloudflare's origin certificate, in which case the address list in it is refreshed.
#   redirects.caddy  The redirect from www and the other domain names to the main address, unless
#                  "deploy/cloudflare.sh redirects on" has handed the redirects to Cloudflare.
#   sites.caddy    The charity's other sites on this server (SITES in deploy/.env), each served once its Cloudflare
#                  origin certificate is in deploy/local/sites/ ("deploy/cloudflare.sh site DOMAIN" puts it there).
set -euo pipefail
DIR="${DIR:-/opt/hansardgpt}"
LOCAL="$DIR/deploy/local"
mkdir -p "$LOCAL"

ranges="$( { curl -fsS --max-time 20 https://www.cloudflare.com/ips-v4 && echo && \
             curl -fsS --max-time 20 https://www.cloudflare.com/ips-v6; } 2>/dev/null | tr -s ' \n' ' ' || true)"
# a sanity check, so a broken download never replaces a good list
if [[ "$(wc -w <<<"$ranges")" -ge 10 ]] && grep -qE '^[0-9a-f:./ ]+$' <<<"$ranges"; then
  printf '%s\n' "$ranges" > "$LOCAL/cloudflare-ranges.txt"
fi
saved="$(cat "$LOCAL/cloudflare-ranges.txt" 2>/dev/null || true)"

if [[ -n "$saved" ]]; then
  cat > "$LOCAL/proxies.caddy" <<CADDY
servers {
	trusted_proxies static $saved
	client_ip_headers CF-Connecting-IP
}
CADDY
elif [[ ! -f "$LOCAL/proxies.caddy" ]]; then
  echo "# Cloudflare's addresses could not be fetched yet; requests are credited to the address they come from" \
    > "$LOCAL/proxies.caddy"
fi

if [[ -f "$LOCAL/origin.pem" && -f "$LOCAL/origin.key" && -f "$LOCAL/behind-cloudflare" && -n "$saved" ]]; then
  cat > "$LOCAL/site.caddy" <<CADDY
# behind Cloudflare's proxy (deploy/cloudflare.sh): Cloudflare's origin certificate, and connections that do not
# come through Cloudflare are closed
tls /etc/caddy/local/origin.pem /etc/caddy/local/origin.key
@direct not remote_ip $saved
abort @direct
CADDY
else
  echo "# Caddy gets its own certificate from Let's Encrypt (deploy/cloudflare.sh changes this)" > "$LOCAL/site.caddy"
fi

if [[ -f "$LOCAL/redirects-at-cloudflare" ]]; then
  echo "# Cloudflare redirects www and the other domain names (deploy/cloudflare.sh redirects off changes this)" \
    > "$LOCAL/redirects.caddy"
else
  # the header tells deploy/cloudflare.sh that a redirect came from this server rather than from Cloudflare
  cat > "$LOCAL/redirects.caddy" <<'CADDY'
{$REDIRECT_DOMAINS} {
	header X-Redirect-Source server
	redir https://{$DOMAIN}{uri} permanent
}
CADDY
fi

# The other sites: "domain=container" pairs, the container being the site's app on the shared "sites" network
sites="$(grep -E '^SITES=' "$DIR/deploy/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)"
{
  echo "# The charity's other sites on this server (SITES in deploy/.env); written by deploy/caddy-local.sh"
  for pair in $sites; do
    domain="${pair%%=*}"; app="${pair#*=}"
    if [[ -f "$LOCAL/sites/$domain.pem" && -f "$LOCAL/sites/$domain.key" && -n "$saved" ]]; then
      cat <<CADDY
$domain {
	# Cloudflare's origin certificate for this domain; connections that do not come through Cloudflare are closed
	tls /etc/caddy/local/sites/$domain.pem /etc/caddy/local/sites/$domain.key
	@direct not remote_ip $saved
	abort @direct
	import other_site $app
}
CADDY
    else
      echo "# $domain: not served yet (its certificate is added with: bash $DIR/deploy/cloudflare.sh site $domain)"
    fi
  done
} > "$LOCAL/sites.caddy"
