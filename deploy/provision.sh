#!/usr/bin/env bash
# Provision Where They Stand on a fresh Ubuntu 24.04 server (run as root).
#
#   curl -fsSL https://raw.githubusercontent.com/rj-mitchell/wheretheystand/main/deploy/provision.sh | bash
#   (or copy this file to the server and run: sudo bash provision.sh)
#
# What it does:
#   1. installs Docker and git, turns on automatic security updates, a firewall, and
#      fail2ban (blocks addresses that keep failing to log in, while password logins are on)
#   2. clones the repository to /opt/hansardgpt
#   3. asks for your domain and Anthropic API key and writes deploy/.env
#   4. builds and starts the app behind Caddy (automatic HTTPS)
#   5. starts the one-off bootstrap in the background (downloads ~5 GB of Hansard
#      since 2010, ingests, indexes; embeddings continue in later syncs)
#   6. installs the twice-daily sync timer and the nightly backup timer
# The site is public. An admin token (for /api/admin/usage and /api/admin/sync) is
# generated into deploy/.env. For an invite-only site instead, see the README.
set -euo pipefail

REPO="${REPO:-https://github.com/rj-mitchell/wheretheystand.git}"
BRANCH="${BRANCH:-main}"
DIR=/opt/hansardgpt

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

echo "==> Installing Docker and git"
apt-get update -y
apt-get install -y ca-certificates curl git openssl
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

echo "==> Turning on automatic security updates, the firewall (SSH, HTTP and HTTPS only) and fail2ban"
apt-get install -y unattended-upgrades ufw fail2ban python3-systemd
# read SSH logins from the system journal (some server images have no /var/log/auth.log)
printf '[sshd]\nenabled = true\nbackend = systemd\n' > /etc/fail2ban/jail.d/where-they-stand.conf
systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban || echo "WARNING: fail2ban did not start; carrying on (deploy/check.sh will flag it)."
dpkg-reconfigure -f noninteractive unattended-upgrades
# a file read last, so no image default can switch security updates off again; and the timers that act on it
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/99zz-auto-upgrades
systemctl unmask apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
# (sshd -T can fail on Ubuntu 24.04 when ssh is socket-activated; then the default port 22 is used)
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}' || true)"
ufw allow "${SSH_PORT:-22}/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Fetching Where They Stand into $DIR"
if [[ -d $DIR/.git ]]; then
  git -C "$DIR" pull --ff-only
else
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR/deploy"

if [[ ! -f .env ]]; then
  read -rp "Main domain name pointing at this server (e.g. wheretheystand.uk): " DOMAIN
  read -rp "Other domain names that should redirect to it, space-separated (e.g. wheretheystand.co.uk wheretheystand.org.uk), or blank: " OTHERS
  REDIRECTS="www.$DOMAIN"
  for d in $OTHERS; do REDIRECTS="$REDIRECTS, $d, www.$d"; done
  read -rsp "Anthropic API key (leave blank for sources-only mode): " KEY
  echo
  umask 077
  printf 'DOMAIN=%s\nREDIRECT_DOMAINS="%s"\nHANSARD_ANTHROPIC_API_KEY=%s\nHANSARD_ADMIN_TOKEN=%s\n' \
    "$DOMAIN" "$REDIRECTS" "$KEY" "$(openssl rand -hex 24)" > .env
fi

echo "==> Adding swap space (spare memory on disk for large updates)"
bash "$DIR/deploy/swap.sh" || echo "WARNING: could not add swap; carrying on."

echo "==> Writing the web server's own settings (Cloudflare's addresses, the certificate)"
bash "$DIR/deploy/caddy-local.sh"

echo "==> Building and starting"
docker compose up -d --build

echo "==> Starting the initial data load in the background (log: /var/log/hansard-bootstrap.log)"
nohup docker compose run --rm -T app hansard bootstrap --embed-minutes 120 > /var/log/hansard-bootstrap.log 2>&1 &

echo "==> Installing the sync and backup timers"
mkdir -p backups
cp hansard-sync.service hansard-sync.timer hansard-backup.service hansard-backup.timer \
   hansard-watchdog.service hansard-watchdog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hansard-sync.timer hansard-backup.timer hansard-watchdog.timer

cat <<MSG

Where They Stand is starting at https://$(grep ^DOMAIN= .env | cut -d= -f2)/
The first data load takes roughly 30-60 minutes (follow /var/log/hansard-bootstrap.log).
Semantic search improves as embeddings complete over the next syncs.

The site is open to everyone. Usage and spend:
  curl -H "Authorization: Bearer \$(grep ^HANSARD_ADMIN_TOKEN= $DIR/deploy/.env | cut -d= -f2)" \\
    https://$(grep ^DOMAIN= .env | cut -d= -f2)/api/admin/usage
MSG
