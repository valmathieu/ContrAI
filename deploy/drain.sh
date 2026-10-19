#!/usr/bin/env bash
# Stop the box's fleet without losing a game: drain it, wait, then stop it.
#
#   sudo deploy/drain.sh
#
# A plain `docker compose stop` writes every game in hand `interrupted`. This
# sends SIGUSR1 instead: the fleet takes no new game, lets the games in hand
# finish within [schedule].max_overrun_minutes, logs `fleet_drained` and holds.
# The stop that follows finds nothing in hand; the container exits 130 and,
# being stopped, stays stopped across a reboot. Start it again with `up -d`.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f deploy/compose.yml -f deploy/compose.fleet.yml)
PROFILE=${CONTRAI_PROFILE:-/etc/contrai/profile.toml}
POLL_S=5

container=$("${COMPOSE[@]}" ps -q scraper)
if [ -z "$container" ]; then
    echo "drain.sh: the scraper is not running" >&2
    exit 1
fi
# Only a fleet drains; a single session ignores the signal and would hold the
# wait until it timed out.
command=$(docker inspect --format '{{join .Args " "}}' "$container")
case "$command" in
    fleet | "fleet "*) ;;
    *)
        echo "drain.sh: the scraper runs '$command', not a fleet;" \
            "stop it with: ${COMPOSE[*]} stop scraper" >&2
        exit 1
        ;;
esac

# The wait is the overrun the fleet grants, plus a margin for the walk out.
overrun=$(sed -n 's/^max_overrun_minutes[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
    "$PROFILE" 2>/dev/null | head -n 1 || true)
wait_s=$(( (${overrun:-30} + 5) * 60 ))

# Follow the journal from now on, before the signal, so that neither a line of
# an earlier drain nor one written while this script starts up is missed.
journal=$(mktemp)
journalctl -t contrai-scraper -f -n 0 -o cat >"$journal" &
follower=$!
trap 'kill "$follower" 2>/dev/null || true; rm -f "$journal"' EXIT
sleep 1

"${COMPOSE[@]}" kill -s USR1 scraper
echo "drain.sh: SIGUSR1 sent; waiting up to $(( wait_s / 60 )) min for fleet_drained"

# Lines the scraper writes that are not JSON (a traceback) are skipped.
progress='fromjson? | select(.event == "fleet_draining" or .event == "game_recorded"
    or .event == "fleet_drained")'
shown=0
deadline=$(( SECONDS + wait_s ))
while :; do
    # Whole lines only: the last one may still be being written.
    lines=$(wc -l <"$journal")
    if [ "$lines" -gt "$shown" ]; then
        sed -n "$(( shown + 1 )),${lines}p" "$journal" | jq -R -c "$progress"
        shown=$lines
    fi
    if head -n "$shown" "$journal" \
        | jq -R -e 'fromjson? | select(.event == "fleet_drained")' >/dev/null; then
        break
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "drain.sh: no fleet_drained after $(( wait_s / 60 )) min; the fleet is" \
            "still draining. Stopping it now writes the games in hand interrupted:" \
            "${COMPOSE[*]} stop scraper" >&2
        exit 1
    fi
    sleep "$POLL_S"
done

"${COMPOSE[@]}" stop scraper
"${COMPOSE[@]}" ps -a scraper
