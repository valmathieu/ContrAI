# Deploying contrai-scraper

For an operator with `sudo` on the host who has not read the code. This guide never names the
site: every command that needs a site address takes it from a shell variable you set from your
private notes and profile.

## 1. What this deploys

Two containers, both described by `deploy/compose.yml`:

- **`vpn`** — gluetun, holding the WireGuard tunnel (`tun0`) and a firewall that lets traffic out
  only through it.
- **`scraper`** — `contrai-scrape run`, headless, as an unprivileged user, with **no network of
  its own**: `network_mode: "service:vpn"` puts it inside the vpn container's network namespace.

**The guarantee:** the scraper has no route but the tunnel. If the tunnel is down, it has no
network at all — it fails closed, it never falls back to the home connection.

**What the egress gate adds is visibility, not the guarantee.** Before every browser session,
before every hop to another table and whenever a table goes quiet, the scraper checks that its
exit address is not the home address, that it sits in the expected country, and that the site's
route leaves through `tun0`. A broken setup is then refused and logged (`egress_blocked`) rather
than merely unlikely.

Nothing else on the host is rerouted: every other service keeps its own routes.

## 2. Prerequisites

- Debian 13 with Docker Engine and the Compose plugin **2.17 or later** (`depends_on.restart`
  needs it): `docker compose version`.
- `jq` and `tcpdump`: `sudo apt install jq tcpdump`.
- A WireGuard `.conf` from the VPN provider, for a **French** server, whose `Endpoint` is an **IP
  address** rather than a host name — gluetun raises its firewall before any name can be resolved.
- The repository tree on the host. Nothing is pushed from a development session, so either clone
  after pushing, or bundle it on the laptop and clone the bundle on the host:

  ```bash
  git bundle create contrai.bundle develop      # on the laptop
  git clone contrai.bundle contrai              # on the host, after copying the file over
  ```

- The private site profile, `profile.toml`. It is never in the repository.

Every `docker compose` command below runs through `sudo`: Compose reads the env files itself, and
they are readable by root only.

## 3. Host preparation

From the repository root, with `wg0.conf` and `profile.toml` in the current directory:

```bash
sudo install -d -m 700 /etc/contrai /etc/contrai/wireguard
sudo install -m 600 wg0.conf /etc/contrai/wireguard/wg0.conf
sudo install -m 600 deploy/vpn.env.example /etc/contrai/vpn.env        # then fill
sudo install -m 600 deploy/contrai.env.example /etc/contrai/contrai.env  # then fill
sudo install -m 400 -o 10001 profile.toml /etc/contrai/profile.toml     # the container user reads it
sudo install -d -m 2750 -o 10001 -g "$(id -gn)" /var/lib/contrai/records
```

`contrai.env` carries the account (`CONTRAI_SCRAPER_EMAIL`, `CONTRAI_SCRAPER_CODE`) and the home
connection's public address (`CONTRAI_HOME_IP`). Left empty, the profile refuses to load.

The profile values that differ on the box:

```toml
[account]
email = "env:CONTRAI_SCRAPER_EMAIL"
verification_code = "env:CONTRAI_SCRAPER_CODE"

[browser]
headless = true
slow_mo_ms = 0

[output]
root = "/var/lib/contrai/records"
raw_root = "/var/lib/contrai/records"

[egress]
home_ip = "env:CONTRAI_HOME_IP"
tunnel_interface = "tun0"
```

and `[schedule]` as you want it. The records directory is setgid to your group, so every record
the scraper writes stays readable by you: records are fetched without `sudo`.

## 4. Build and start

```bash
sudo docker compose -f deploy/compose.yml config -q
sudo docker compose -f deploy/compose.yml build scraper
sudo docker compose -f deploy/compose.yml up -d vpn
sudo docker compose -f deploy/compose.yml run --rm scraper check-profile /etc/contrai/profile.toml
sudo docker compose -f deploy/compose.yml up -d scraper
```

`check-profile` must print `ok` on every line, the egress line first — it shows the exit address,
the country and `route tun0`. When the egress is refused, no browser opens and nothing is sent to
the site.

If gluetun logs an IPv6 address error, remove the IPv6 entries from the `.conf`'s `Address` and
`AllowedIPs` lines.

## 5. Watching it

The scraper writes one JSON object per line to the journal. Everything but the heartbeats:

```bash
journalctl -t contrai-scraper -f -o cat | jq -c 'select(.event != "heartbeat")'
```

The last heartbeat, with every counter:

```bash
journalctl -t contrai-scraper -o cat | jq -c 'select(.event=="heartbeat")' | tail -1
```

Fetching records to the laptop and verifying them there:

```bash
rsync -a <host>:/var/lib/contrai/records/games/ records/games/
uv run contrai verify records/games
```

Exit codes, as the restart count shows them (`sudo docker compose -f deploy/compose.yml ps`):

| Code | Meaning |
| --- | --- |
| 0 | the run's limits were reached |
| 3 | a failure budget was spent — six refused egress checks in a row, or three failed sessions in a row; Docker starts a fresh container |
| 130 | stopped by a signal (`docker compose stop` sends SIGTERM); the game in hand was written `interrupted` |

A restart count that keeps climbing means a budget keeps being spent: read the `egress_blocked`
and `session_failed` lines.

## 6. Proving the confinement

**The exit address.** From inside the scraper's network namespace, an echo service must show the
VPN exit, never the home address:

```bash
sudo docker compose -f deploy/compose.yml run --rm --entrypoint /opt/contrai/venv/bin/python scraper \
  -c "import urllib.request; print(urllib.request.urlopen('https://ipinfo.io/json', timeout=10).read().decode())"
```

**The gate refuses home.** Run the check once with the home address set to the exit address just
printed (`-e` overrides the env file for this one run; Compose's `--env-file` flag would only feed
interpolation):

```bash
EXIT_IP=...   # the "ip" field printed above
sudo docker compose -f deploy/compose.yml run --rm -e CONTRAI_HOME_IP="$EXIT_IP" scraper \
  check-profile /etc/contrai/profile.toml
```

The egress line reads `FAIL … exit_is_home`, and no `login` line follows.

**A tunnel outage.** While a game is being watched, set `WAN_IF`, `SITE_IP` and `VPN_ENDPOINT`
from your private notes, start a capture on the WAN interface, then drop the VPN endpoint in the
host's `DOCKER-USER` chain — a real packet loss, which stopping gluetun would not be:

```bash
sudo tcpdump -ni "$WAN_IF" host "$SITE_IP"                    # in a second terminal
sudo iptables -I DOCKER-USER -d "$VPN_ENDPOINT" -j DROP
```

Expected within `[recorder].stale_after_s`: `table_stale`, then `egress_blocked`, the game written
with `ended.reason = interrupted`, no new `session_started` — and **zero packets** on the capture.
Remove the rule:

```bash
sudo iptables -D DOCKER-USER -d "$VPN_ENDPOINT" -j DROP
```

gluetun reconnects on its own, and the next poll logs `egress_ok` and a new session. Held past six
polls, the process exits 3 and Docker restarts it; the restart count shows it.

## 7. Raw logs

Raw logs are pruned automatically at every session start, once their last write is older than
`[output].raw_retention_days`. Records are never pruned.

## 8. Upgrading

Update the tree, then rebuild and restart the scraper:

```bash
sudo docker compose -f deploy/compose.yml build scraper
sudo docker compose -f deploy/compose.yml up -d scraper
```

A gluetun bump is `sudo docker compose -f deploy/compose.yml pull vpn` then `up -d`; Compose
restarts the scraper with it (`depends_on.restart`).

## 9. If headless is ever refused

The no-code fallback is a headed browser on a virtual display. Add `xvfb` to the Dockerfile's
`apt-get install` line if `which xvfb-run` finds nothing in the image, set
`[browser] headless = false`, and make the command:

```bash
xvfb-run -a /opt/contrai/venv/bin/contrai-scrape run --profile /etc/contrai/profile.toml
```

## 10. Known limits

- The home address is compared as configured. A dynamic residential address that changes silently
  weakens that one check; the country and route checks remain, and the confinement itself depends
  on none of them.
- Chromium runs without its sandbox in the container, as Playwright's image does by default. A
  seccomp profile is a hardening follow-up.
