# Putting Where MPs Stand online (Hostkey, London)

This guide takes you from a newly provisioned server to a live site at **wheretheystand.uk**, step by step. It is
written for your Hostkey virtual server in London, running Ubuntu 24.04, which you log in to with the root password
Hostkey gave you. Allow about an hour, most of it waiting for the Hansard data to load.

You will need:
- the server's **IP address** and **root password**: they are in the email Hostkey sent when the server was
  ready, and in your Hostkey control panel;
- the three domains: wheretheystand.uk, wheretheystand.co.uk and wheretheystand.org.uk;
- your Anthropic API key;
- the code on GitHub's `main` branch (step 1).

---

## 1. Put the latest code on `main` (on GitHub, two clicks)

The server installs whatever is on the `main` branch of <https://github.com/rj-mitchell/wheretheystand>. When a
change is ready it comes as a pull request: open it, press **Squash and merge**, then **Confirm squash and merge**.

## 2. Point the domains at the server

At the company where you registered the domains, open each domain's **DNS settings** and add these records,
using your server's IP address. Remove any existing "parking" **A** records first, but leave any **MX** or **TXT**
records alone: those deliver your email to admin@wheretheystand.uk.

| Domain | Type | Name / host | Value |
|---|---|---|---|
| wheretheystand.uk | A | `@` | your server's IP |
| wheretheystand.uk | A | `www` | your server's IP |
| wheretheystand.co.uk | A | `@` | your server's IP |
| wheretheystand.co.uk | A | `www` | your server's IP |
| wheretheystand.org.uk | A | `@` | your server's IP |
| wheretheystand.org.uk | A | `www` | your server's IP |

**If your domains are on Cloudflare:** set every one of these records to **DNS only** (the grey cloud), not
**Proxied** (the orange cloud, Cloudflare's default), for now. While proxied, `nslookup` shows Cloudflare's addresses
instead of your server's, and the site cannot get its first HTTPS certificate. Once the site is working, the main
address can be put behind Cloudflare's proxy: see [Cloudflare protection](#cloudflare-protection-optional) below.

Changes usually take effect within an hour. To check, run `nslookup wheretheystand.uk` on your computer (in
Terminal or PowerShell): it should print your server's IP address. Wait until it does before step 4, so that the
HTTPS certificates can be issued.

## 3. Log in to the server with its password

On Windows, open **Terminal** (or PowerShell); on a Mac, open **Terminal**. Type, using your server's IP address:

```
ssh root@YOUR.SERVER.IP
```

- The first time, it asks whether to trust the server: type `yes` and press Return.
- It then asks for the password. Paste the root password from Hostkey (in Windows Terminal, right-click pastes;
  on a Mac, Cmd+V) and press Return. **Nothing appears while you type or paste a password; that is normal.**

You are now typing commands on the server. Check it is running Ubuntu 24.04:

```
grep PRETTY /etc/os-release
```

It should say `Ubuntu 24.04`. If it says anything else, stop here and reinstall the server with Ubuntu 24.04 from
the Hostkey control panel.

Then choose a new root password of your own, because the one Hostkey emailed has been sent in plain text:

```
passwd
```

Type the new password twice (again, nothing appears). Keep it in your password manager. To leave the server at
any point, type `exit`.

## 4. Run the setup script

On the server, run:

```
curl -fsSL https://raw.githubusercontent.com/rj-mitchell/wheretheystand/main/deploy/provision.sh -o provision.sh
bash provision.sh
```

It asks three questions:
1. **Main domain name:** `wheretheystand.uk`
2. **Other domain names to redirect:** `wheretheystand.co.uk wheretheystand.org.uk` (the `www` versions are added
   automatically).
3. **Anthropic API key:** paste it. Nothing appears as you paste; that is deliberate. Press Return.

The script then:
- installs Docker, turns on automatic security updates, sets up a firewall that allows only SSH and the web, and
  installs fail2ban, which blocks anyone who keeps guessing the password;
- builds and starts the site, with HTTPS certificates obtained automatically;
- starts loading Hansard since 2010 in the background (30–60 minutes);
- sets up the twice-daily update and a nightly backup.

At the end it prints a command for checking usage and spend. Keep it somewhere safe; it contains your admin
token.

## 5. Recommended: switch to a key instead of a password

Logging in with a password works, but a **key** (a pair of files on your computer) is much safer, because there
is nothing to guess. Once it is set up, you can turn password logins off. Do this from your **own computer**, not
the server: if you are still logged in to the server, type `exit` first.

**a. Make the key (once).** In Terminal or PowerShell:

```
ssh-keygen -t ed25519
```

Press Return at each question. (A passphrase is optional; if you set one, you type it instead of the server
password.)

**b. Copy it to the server.** You will be asked for the root password one last time.

Windows (PowerShell or Terminal):

```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@YOUR.SERVER.IP "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Mac:

```
ssh-copy-id root@YOUR.SERVER.IP
```

**c. Check the key works.** Run `ssh root@YOUR.SERVER.IP` again. It should log you straight in without asking for
the server password. **Do not go on to step d unless it does.**

**d. Turn off password logins.** Stay logged in, open a *second* Terminal window as a safety net, and log in there
too. Then, in one of them, run:

```
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/00-keys-only.conf
systemctl reload ssh
```

In a third window, check `ssh root@YOUR.SERVER.IP` still logs you in. If it does, you are done. If it does not,
use one of the windows still logged in to undo it with `rm /etc/ssh/sshd_config.d/00-keys-only.conf && systemctl
reload ssh`, and ask for help.

If you are ever locked out (a lost laptop, say), the Hostkey control panel has a web console and can reset the
root password.

## 6. Check everything is working

On the server, run:

```
bash /opt/hansardgpt/deploy/check.sh
```

It changes nothing. It lists what is working (✓) and anything that needs attention (✗), with what to do about each.
Run it again at any time.

## 7. Watch the first data load

On the server (log in again with `ssh root@YOUR.SERVER.IP` if you are not already):

```
tail -f /var/log/hansard-bootstrap.log
```

Press Ctrl+C to stop watching (the load carries on). When it finishes, open **https://wheretheystand.uk**.
Searching works as soon as the load completes. Meaning-based matching is then built in the background during
each twice-daily update, newest records first; on 8 cores the whole archive takes roughly a day. The
[status page](https://wheretheystand.uk/status) shows its progress.

---

## Day to day

The easiest way to keep an eye on the site is the admin page, **https://wheretheystand.uk/admin**. Paste your
admin token (below) to see spending, reports from readers, feedback, visits, recent briefings and update
history. You can close reports there (adding a public note for the [corrections page](https://wheretheystand.uk/corrections)
if something was put right) and withdraw a briefing. A withdrawal reason is shown publicly.

| To… | Run on the server |
|---|---|
| See usage, spend, visits and feedback | the admin page, or the `curl` command printed at the end of setup |
| See the update history | `curl -H "Authorization: Bearer YOUR_ADMIN_TOKEN" https://wheretheystand.uk/api/admin/sync` |
| Install a new version of the site | merge the latest changes into `main` on GitHub (step 1), then on the server: `bash /opt/hansardgpt/deploy/update.sh` (it finishes with the health check) |
| Restart the site | `cd /opt/hansardgpt/deploy && docker compose restart` |
| Change the API key | the command below |
| Change the "Support this site" link (it points to https://ko-fi.com/wheretheystand) | add `HANSARD_DONATE_URL=https://…` to `/opt/hansardgpt/deploy/.env`, then `docker compose up -d` in the same folder |
| List recent briefings | `curl -H "Authorization: Bearer YOUR_ADMIN_TOKEN" https://wheretheystand.uk/api/admin/briefings` |
| Feature a briefing (listed on the Topics page and found by search engines) | the admin page: "Feature" next to a recent briefing, or paste its address under "Featured briefings" |
| Write the topic briefings now, rather than waiting for the twice-daily update (about 11p each) | `cd /opt/hansardgpt/deploy && docker compose exec app hansard topics refresh` (add `--only cost-of-living` for one) |
| See each topic's state | the admin page, or `docker compose exec app hansard topics list` |
| Collect the devolved parliaments' records now, rather than a few dozen files per update (free; about 4,000 files back to 1998, a few hours) | `cd /opt/hansardgpt/deploy && docker compose exec -d app hansard devolved --files 5000` |
| Rebuild the committee pages now, rather than waiting for the weekly update (free; the first time takes about two hours, because each committee's list of inquiries is fetched once from a slow service; later rebuilds take under an hour) | `cd /opt/hansardgpt/deploy && docker compose exec -d app hansard committees` |
| Fetch the newest Register of Members' Financial Interests now (free; also done by every update) | `cd /opt/hansardgpt/deploy && docker compose exec app hansard interests` |
| Collect committee evidence transcripts back to 2014 and make them searchable by meaning now, rather than 500 per update (free; once; about 8,000 sessions, several hours: one request at a time, then the embedding) | `cd /opt/hansardgpt/deploy && docker compose exec -d app sh -c 'mkdir -p /data/logs && hansard evidence > /data/logs/evidence.log 2>&1'` |
| Read every past edition of the Commons register of interests (since 2000) now, rather than 40 per update (free; once; about 420 editions, 20–30 minutes) | `cd /opt/hansardgpt/deploy && docker compose exec -d app hansard interests --archive 500` |
| Load the donations to MPs now, rather than at the weekly update (free; about a minute) | `cd /opt/hansardgpt/deploy && docker compose exec app hansard donations` |
| Load the all-party groups now, rather than at the weekly update (free; a few seconds) | `cd /opt/hansardgpt/deploy && docker compose exec app hansard groups` |
| See which of these background jobs are still running (no output means all have finished) | `cd /opt/hansardgpt/deploy && docker compose exec app sh -c 'for p in /proc/[0-9]*; do tr "\0" " " < $p/cmdline 2>/dev/null; echo; done \| grep "[h]ansard "'` |
| Load the 1997–2010 records now, rather than at the next update (once, after installing the version that adds them; free; a few hours) | `sudo systemctl start --no-block hansard-sync.service`, then watch with `journalctl -u hansard-sync -f` |
| See whether an update is running, and whether the last one finished | `systemctl status hansard-sync --no-pager` (`active` means running; `failed` with `status=137` means it ran out of memory: run `bash /opt/hansardgpt/deploy/swap.sh`, then start it again as above) |
| See how much is still to do | `cd /opt/hansardgpt/deploy && docker compose run --rm -T app hansard status` (`files_unembedded` falls to 0 as meaning-based search catches up, up to 4 hours of it per update) |
| Rebuild the meaning-search index for today's number of passages: quicker searches that find more of the closest passages (once, after meaning-based search has caught up; free; about 30–60 minutes, the site works throughout and the twice-daily update waits for it) | `cd /opt/hansardgpt/deploy && docker compose exec -d app sh -c 'mkdir -p /data/logs && hansard reindex-vectors > /data/logs/reindex.log 2>&1'`, then `docker compose exec app cat /data/logs/reindex.log` when it has finished (it ends by saying to restart), then `bash /opt/hansardgpt/deploy/update.sh` |
| Check every vote for a whip report in one go, rather than 1,500 per update (free; about 3–4 hours) | `cd /opt/hansardgpt/deploy && docker compose exec -d app hansard whips --count 20000` |
| Set or change the Companies House API key (for company roles; free from developer.company-information.service.gov.uk) | `cd /opt/hansardgpt/deploy && read -rsp "Companies House API key: " K && echo && sed -i -e '/^HANSARD_COMPANIES_HOUSE_KEY=/d' -e '$a\' .env && printf 'HANSARD_COMPANIES_HOUSE_KEY=%s\n' "$K" >> .env && unset K && docker compose up -d` |
| Look up every member's company roles now, rather than 400 per update (free; about 2–3 hours) | `cd /opt/hansardgpt/deploy && docker compose exec -d app hansard directorships --count 4000` |
| See the company roles found for members whose name contains some text | `cd /opt/hansardgpt/deploy && docker compose exec app hansard directorships --show "Smith"` |
| Remove a company role wrongly matched to a member | ask Claude: it adds the Companies House record to `data_static/directorships_exclude.csv`, removed at the next update |
| Check that early day motions can be read from Parliament (free) | `cd /opt/hansardgpt/deploy && docker compose exec app hansard edms --check` |
| Collect all early day motions since 1997 in one go, in the background (free; about 8–10 hours the first time, one request at a time) | `cd /opt/hansardgpt/deploy && docker compose exec -d app hansard edms --months 400 --signatures 60000` |
| Add, change or remove a topic | edit `config/topics.toml` in the code (ask me), then install it as a new version. Never change a published topic's `slug`: it is its address |
| Withdraw a briefing (after a complaint, say) | `curl -X POST -H "Authorization: Bearer YOUR_ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"reason": "complaint about attribution"}' https://wheretheystand.uk/api/admin/briefings/THE_ID/withdraw` |

Your admin token is in `/opt/hansardgpt/deploy/.env` (`HANSARD_ADMIN_TOKEN=`).

**To change the API key**, run this on the server. It asks for the key without showing it (paste it and press
Return), saves it and restarts the site:

```
cd /opt/hansardgpt/deploy && read -rsp "Anthropic API key: " K && echo && sed -i "s|^HANSARD_ANTHROPIC_API_KEY=.*|HANSARD_ANTHROPIC_API_KEY=$K|" .env && unset K && docker compose up -d
```

Then run `bash /opt/hansardgpt/deploy/check.sh`: it should say "Anthropic accepts the API key".

## Cloudflare protection (optional)

Cloudflare's proxy (the orange cloud) puts Cloudflare between visitors and the server, so floods of automated
requests are absorbed before they reach it. For **wheretheystand.uk**, the server then uses a certificate from
Cloudflare, closes any connection that does not come through Cloudflare, and still sees each visitor's own address
(Cloudflare passes it on), so the per-visitor daily limit keeps working. **www.wheretheystand.uk**,
**wheretheystand.co.uk** and **wheretheystand.org.uk** (with their `www`) are redirected to wheretheystand.uk by
Cloudflare itself, so no DNS record shows the server's address any more. (Old DNS records can still reveal it, so
this protects against casual abuse and heavy load more than a determined attack on the server itself.)

1. **Install the latest version** (`bash /opt/hansardgpt/deploy/update.sh`). It fetches Cloudflare's list of
   addresses; that is harmless while nothing is proxied.
2. **Put all three domains on Cloudflare.** For each of wheretheystand.uk, wheretheystand.co.uk and
   wheretheystand.org.uk: at dash.cloudflare.com, choose **Add a domain**, enter it, pick the **Free** plan, and check
   the records it found: the **A** records for `@` and `www` from step 2 (set them to **DNS only** for now) and every
   **MX** and **TXT** record (email). Then, at the company where you registered the domain, replace its nameservers
   with the two Cloudflare gives you, and wait for Cloudflare to say the domain is **Active** (usually within an
   hour; it emails you).
3. **Settings in Cloudflare.** For **wheretheystand.uk**:
   - **SSL/TLS → Overview**: choose **Full (strict)**. (Not "Flexible": that sends visitors round in circles.)
   - **SSL/TLS → Edge Certificates**: turn on **Always Use HTTPS**.
   - **Security → Settings** (filter by *Client-side abuse*): turn off **Email Address Obfuscation** (on by default;
     it rewrites pages).
   - **Security → Settings** (filter by *Bot traffic*): turn off **Bot fight mode** (it can block the public API
     and sets a cookie), set **Block AI bots** to *Do not block*, and turn off **Managed robots.txt**: the site
     publishes readable text for AI assistants and has its own robots.txt. If **AI Crawl Control** (left-hand menu)
     lists any crawlers as *Block*, set them to *Allow*.
   - **Speed → Settings → Content Optimization**: make sure **Rocket Loader** is off (off by default).
   - **Web Analytics** (in the account's left-hand menu, under *Analytics & Logs*, not inside the domain): once the
     domain is proxied, Cloudflare turns it on by itself. Find wheretheystand.uk in the list, choose **Manage site**,
     and under automatic setup choose **Disable** (it would add a tracking script; the site has its own cookie-free
     counts).

   - For speed (all free): **Speed → Settings**: **HTTP/3 (with QUIC)** and **0-RTT Connection Resumption** on,
     and **HTTP/2 to Origin** on; **Caching → Tiered Cache**: **Smart Tiered Caching** on. Leave **Caching → Configuration**
     at *Standard* and *Respect existing headers*, and add no "Cache everything" rule: pages carry live data, and the
     site already tells Cloudflare which files can be kept for a year.

   For **wheretheystand.co.uk** and **wheretheystand.org.uk**, only **SSL/TLS → Full (strict)** matters: Cloudflare
   answers them itself, so nothing else reaches the server.
4. **Create the origin certificate** (wheretheystand.uk only): **SSL/TLS → Origin Server → Create Certificate**.
   Keep *Generate private key and CSR with Cloudflare* and key type **RSA (2048)**. Under hostnames, **remove
   `*.wheretheystand.uk`** so that only `wheretheystand.uk` is listed. Validity: **15 years**. Press **Create** and
   leave the page open, with the format on **PEM**: the private key is shown only once.
5. **Turn the proxy on for the main address**: in wheretheystand.uk's **DNS → Records**, edit the **A** record named
   `wheretheystand.uk` (`@`) and set it to **Proxied** (orange cloud). If there is an **AAAA** record for `@`, set it
   to Proxied too (or delete it), or visitors using IPv6 would be turned away in the next step. The site carries on
   working (Cloudflare uses the server's current certificate until the next step).
6. **Switch the server over.** A few minutes later, on the server, run:

   ```
   bash /opt/hansardgpt/deploy/cloudflare.sh on
   ```

   Paste the **Origin Certificate** from the Cloudflare page and press Return, then the **Private Key** and press
   Return. It checks they belong together and are for wheretheystand.uk, switches the server over, and checks the
   site through Cloudflare. If that fails, it puts everything back and says what to check.
7. **Have Cloudflare do the redirects.** For each domain below, first open **Rules → Redirect Rules** (in some
   accounts, **Rules → Overview → Create rule → Redirect Rule**) and create a rule:
   - **Rule name**: `To wheretheystand.uk`.
   - **If incoming requests match**: for **wheretheystand.uk**, choose *Custom filter expression*: field
     **Hostname**, operator **equals**, value `www.wheretheystand.uk`. For **wheretheystand.co.uk** and
     **wheretheystand.org.uk**, choose **All incoming requests**.
   - **Then**: type **Dynamic**, expression `concat("https://wheretheystand.uk", http.request.uri.path)`, status
     code **301**, and tick **Preserve query string**.
   - Press **Deploy**.

   Then, in the same domain's **DNS → Records**, edit each record the rule covers (for wheretheystand.uk, just
   `www`; for the other two, `@` and `www`): set it to **Proxied**, and change its **IPv4 address** to `192.0.2.1`
   (an address that goes nowhere: Cloudflare answers these names itself, and the server's address is no longer
   shown). Delete any **AAAA** records for those names. Leave MX and TXT records alone.
8. **Hand the redirects over.** A few minutes later, on the server, run:

   ```
   bash /opt/hansardgpt/deploy/cloudflare.sh redirects on
   ```

   It checks that Cloudflare redirects each name to the same page on wheretheystand.uk, then stops the server doing
   it. If any name is not ready (a brand-new domain's Cloudflare certificate can take a little while), it changes
   nothing and says which; run it again later.
9. **Check**: `bash /opt/hansardgpt/deploy/check.sh` should say "goes through Cloudflare's proxy" and that each name
   redirects. Then ask a question on the site and check that the briefing appears as it is written.

**To undo the redirects**, point each of those records back at the server's IP address (**DNS only**), delete the
redirect rules, then run `bash /opt/hansardgpt/deploy/cloudflare.sh redirects off`. **To undo the proxy** for the
main address, run `bash /opt/hansardgpt/deploy/cloudflare.sh off` on the server **first**, then set its A record back
to **DNS only**. The certificate is kept, so `cloudflare.sh on` switches back without pasting it again (add
`--replace` to paste a new one). If the site is ever under attack, **Security → Settings → Under Attack mode** in
Cloudflare asks every visitor's browser to pass a short check first.

## Backups

Everything can be rebuilt from public sources except the saved briefings, reports from readers, feedback, usage and
visit counts. The setup script backs these up **every night at 3.30am**, keeping the last 14 days, in
`/opt/hansardgpt/deploy/backups` on the server.

Those copies protect against mistakes, but not against losing the whole server, so each night's backup is also
copied to a **Backblaze B2** bucket once it is set up (the last 60 nights are kept there; they are small, well
within B2's free 10 GB).

### Setting up the off-site copy (once)

1. At backblaze.com, create a **private** B2 bucket and an **application key** allowed to access only that bucket.
2. On the server, run this. It asks for the bucket name, the keyID and the applicationKey; the key is not shown as
   you paste it, and is saved only in the server's settings file:

   ```
   cd /opt/hansardgpt/deploy && read -rp "B2 bucket name: " B && read -rp "B2 keyID: " I && read -rsp "B2 applicationKey: " K && echo && sed -i -e '/^HANSARD_B2_/d' -e '$a\' .env && printf 'HANSARD_B2_BUCKET=%s\nHANSARD_B2_KEY_ID=%s\nHANSARD_B2_APPLICATION_KEY=%s\n' "$B" "$I" "$K" >> .env && unset B I K
   ```

3. Check the key works: `docker compose run --rm -T app hansard offsite` should say `"ok": true`.
4. Make a backup now, which also sends it off-site:
   `docker compose run --rm -T app hansard backup --to /backups`. It should end "Off-site copy: stored in …".

From then on it happens every night. The admin page shows a warning if a backup or its off-site copy fails.

To get a backup back from B2 (if the server is lost): in the Backblaze website, open the bucket, download the
newest `where-they-stand-….tar.gz`, copy it into `/opt/hansardgpt/deploy/backups/` on the new server, and restore it
as below.

You can still copy backups to your own computer if you like. In Terminal or PowerShell on your computer:

```
scp "root@YOUR.SERVER.IP:/opt/hansardgpt/deploy/backups/*.tar.gz" .
```

To make a backup straight away: `cd /opt/hansardgpt/deploy && docker compose run --rm -T app hansard backup --to /backups`.

To put a backup back (replacing what is there now), on the server:

```
cd /opt/hansardgpt/deploy
docker compose stop app
docker compose run --rm -T app hansard restore /backups/where-they-stand-2026-10-11.tar.gz --yes
docker compose start app
```

(Use the date of the backup you want; `ls backups` lists them.)

## Spending limits

Every 10 new briefings in a day, a visitor sees a short note on what the site costs, with your Ko-fi link; if they
go through to Ko-fi, they are not reminded again that day. As a backstop against automated use, a visitor can have
at most 40 new briefings a day. The whole site has budgets of $10 a day and $150 a month. To change any of these, edit the `[usage]` section of `config/settings.toml`, then install it as a new version (see the table).
