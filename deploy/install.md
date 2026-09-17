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
- A WireGuard `.conf` from the VPN provider, for a server in whichever country you will set as
  `[egress].expected_country`, and whose `Endpoint` is an **IP address** rather than a host name —
  gluetun raises its firewall before any name can be resolved. Keep the provider's fixed endpoint
  rather than a provider integration that picks its own server: a server that reconnects elsewhere
  invalidates the outage test in §6 and moves the address the gate expects.
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

Records are plain JSONL, so any file transfer does instead of `rsync` when the host has no SSH —
what matters is only that they arrive whole. Verification is always done off the host: nothing in
`contrai verify` belongs in the deployment, and the image does not carry it.

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
  -c "import tomllib, urllib.request as u; \
url = tomllib.load(open('/etc/contrai/profile.toml','rb'))['egress']['probe_url']; \
print(u.urlopen(url, timeout=10).read().decode())"
```

It asks the same service the gate asks, taken from the profile, so this check and the gate can
never disagree about which echo service is in use — see §10 on choosing one.

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

Update the tree first, and note that the host may have nothing to pull *from*: §2's clone can come
from a bundle, and a host administered through a console rather than SSH has no usable remote at
all. An incremental bundle carries the new commits and nothing else, so it stays small enough for
whatever file transfer the host does have:

```bash
git bundle create update.bundle <the host's commit>..<branch>   # on the laptop
git bundle verify update.bundle                                 # on the host, once copied over
git fetch ./update.bundle <branch>
git merge --ff-only FETCH_HEAD
```

`git bundle verify` names the commit the bundle needs; if the host does not have it, bundle the
whole branch instead, which is self-contained. `--ff-only` is deliberate: a host tree that has
diverged should stop and be looked at rather than merged blind. Local edits to files the update
does not touch survive the fast-forward, which is what keeps a deliberately pinned image tag in
place.

Then rebuild and restart the scraper:

```bash
sudo docker compose -f deploy/compose.yml build scraper
sudo docker compose -f deploy/compose.yml up -d scraper
```

The image carries the code, so updating the tree changes nothing until `build` has run. Re-run
`check-profile` (§4) whenever the update touches `profile.example.toml`: `[wire.fields]` must bind
the parser's field names **exactly**, so a profile written against an older revision is refused
outright rather than half-applied.

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
- The echo service is a dependency of the gate, and a free-tier exit is shared with many other
  users. A service that rate-limits per address answers `429` to all of them, which the gate reads
  as `probe_failed` and refuses — correctly, but for the wrong reason, and six refusals in a row
  end the process. Pick a service that returns the address and the country as plain JSON and does
  not meter per address; `[egress].probe_url` with `probe_ip_field` and `probe_country_field` makes
  the change a config edit, not a code change.

## 11. Validation (2026-09-20)

Measured end to end on the target host — Debian 13, Docker Engine 29.8.0, Compose v5, reached
through a web console rather than SSH — with the scraper sharing a WireGuard sidecar's network
behind a free-tier exit in a country other than the operator's. Every figure below was read from
the journal or from `contrai verify`. No address, host name or site string is recorded here.

| Check | Result |
| --- | --- |
| Profile on a laptop, headed, through a desktop VPN | `check-profile` all eleven lines `ok`. A two-game run ended on `max_games`: 2 games, 15 rounds, 26 min, 10 table visits, 8 rejected, no session failure |
| V1 — `check-profile` in the container, headless | All eleven lines `ok`, exit 0. The egress line showed the expected country and `route tun0`; the marker line proves the image's colour-emoji font |
| V2 — one hour of the service | 1 h 29 m: 8 games, 41 rounds (**27.6 rounds/h**), 8 tables seated, **2 sockets opened, 0 closed**, 0 restarts |
| V3 — the gate refuses the home address | `FAIL … exit_is_home`, exit 1. No browser opened, and the refusal line carries no address |
| V4 — a tunnel outage under a live game | **0 packets** on the WAN capture. Detail below |
| V5 — 24 hours unattended | 117 games, 604 rounds, **0 restarts**. Detail below |

**V4 — the outage.** The VPN endpoint was dropped in the host's `DOCKER-USER` chain while a game
was being watched, with a capture running on the WAN interface filtered to the site's addresses.
A control capture on a neutral address first proved the interface and the filter, so the zero
below is a real zero and not a mis-aimed capture.

| Measure | Result |
| --- | --- |
| Drop → `egress_blocked` | 203 s (`[recorder].stale_after_s` plus the probe timeout) |
| The game in hand | written with `ended.reason = interrupted` |
| **Packets to the site on the WAN** | **0** |
| Rule removed → `egress_ok` | 79 s — bounded by the poll, not by the tunnel: gluetun reconnected unaided |
| → next `table_seated` | 116 s |
| The blocked-poll ladder | `attempt` 1 … 6, spaced 5 min 20 s (a 5 min poll plus the probe timeout) |
| Budget spent → exit 3 → fresh container polling | 21 s |
| One full block cycle | 27 min; 12 cycles observed over ≈14 h of held outage |
| Window close → `schedule_idle` | +1 min 43 s, with `next_opening` exact |
| Window open → `schedule_resume` | +1 min 49 s |

**V5 — 24 hours unattended.** No intervention. 25 h 12 m elapsed, of which 6 h 50 m was scheduled
idle, leaving **18 h 22 m** of active watching.

| Measure | Result |
| --- | --- |
| Restarts | **0** |
| Games / rounds | **117 / 604** → **32.9 rounds/h** active |
| Tables seated / rejected | 118 / 64 — 59 not a tournament, 4 unreadable snapshot, 1 too far along |
| Orientation mismatches | **0** |
| Stale-snapshot refusals | 142 over ≈210 hops — under one per hop, as designed |
| Sockets opened / closed | **8 / 0** — two per session, **no reconnect in 18 active hours** |
| Failed score reads | 0 |
| Unexplained heartbeat gaps over 180 s | **0**, across 1064 timeline lines |
| Session failures | 2, both on the table-hop control, against a budget of 3; the shift recovered unaided and the fault is fixed |

`contrai verify` over the 135 records the host had accumulated: **129 `partial`, 6 `suspect`**;
685 rounds, **679 `partial`, 6 `suspect`**. A `partial` round is one where every comparable field
agreed — `verified` is unreachable for an observed record, because the table folds the last-trick
bonus into the row's card points and never names the side that took it, which the verifier records
as unchecked rather than as a fault. The six suspects are all disagreements about how a sweep or a
tied round is *scored*, not capture faults: the wire data matched the replay in every other
respect.
