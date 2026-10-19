"""Pins the deployment files: no real address, no private file in the image."""

import importlib.metadata
import pathlib
import re

import contrai_scraper

#: The repository root, from the installed package (src/contrai_scraper → root).
ROOT = pathlib.Path(contrai_scraper.__path__[0]).parents[3]
DEPLOY = ROOT / "deploy"

#: RFC 5737 documentation ranges, loopback and the unspecified address.
_ALLOWED = re.compile(r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.\d{1,3}$|^127\.|^0\.0\.0\.0$")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

#: What must never enter the build context, even inside an allowed directory.
_LOCAL_MATERIAL = (
    "**/profile.toml", "**/accounts.toml", "**/.env", "**/*.env", "**/records", "**/raw",
)


class TestDeployFiles:
    def test_no_file_holds_a_real_address(self):
        # The home address, the VPN endpoint and the site's address all belong
        # to the operator's private notes; a tracked file names none of them.
        found = {
            (path.name, address)
            for path in [*DEPLOY.iterdir(), ROOT / "packages/contrai-scraper/profile.example.toml"]
            if path.is_file()
            for address in _IPV4.findall(path.read_text(encoding="utf-8"))
            if not _ALLOWED.match(address)
        }
        assert found == set()

    def test_local_material_is_excluded_after_the_allow_list(self):
        # Later lines win in a dockerignore: an exclusion written above the
        # allow-list would be undone by it, and the profile would ship.
        lines = [
            line.strip()
            for line in (DEPLOY / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        last_allow = max(index for index, line in enumerate(lines) if line.startswith("!"))
        placed = {
            pattern: pattern in lines and lines.index(pattern) > last_allow
            for pattern in _LOCAL_MATERIAL
        }
        assert placed == dict.fromkeys(_LOCAL_MATERIAL, True)

    def test_the_image_tag_matches_the_locked_playwright(self):
        # The image's browsers belong to one Playwright version; any other
        # installed version cannot find them and fails at the first launch —
        # on a headless box, long after the lock was bumped.
        dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
        tag = re.search(r"^FROM mcr\.microsoft\.com/playwright/python:v(\d+\.\d+\.\d+)-",
                        dockerfile, re.M)
        assert tag is not None and tag.group(1) == importlib.metadata.version("playwright")

    def test_the_image_reads_the_profile_where_compose_mounts_it(self):
        # Drifting apart, the container would start and then fail to find the
        # one file it cannot run without.
        target = "/etc/contrai/profile.toml"
        command = next(
            line for line in (DEPLOY / "Dockerfile").read_text(encoding="utf-8").splitlines()
            if line.startswith("CMD")
        )
        items = re.findall(r"^\s*-\s*(\S+)\s*$",
                           (DEPLOY / "compose.yml").read_text(encoding="utf-8"), re.M)
        assert (target in command, any(item.endswith(f":{target}:ro") for item in items)) == (
            True, True
        )

    def test_the_fleet_reads_its_files_where_compose_mounts_them(self):
        # The override's command names two files: the profile, mounted by
        # compose.yml, and the accounts, mounted by the override itself. A path
        # in the command that no layer mounts read-only is a fleet that starts
        # and then cannot log anyone in.
        def items(name):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            return re.findall(r"^\s*-\s*(\S+)\s*$", text, re.M)

        fleet = items("compose.fleet.yml")
        named = [item for item in fleet if item.startswith("/etc/contrai/") and ":" not in item]
        mounted = {item.split(":")[1] for item in [*items("compose.yml"), *fleet]
                   if item.endswith(":ro")}
        assert (fleet[0], named, set(named) <= mounted) == (
            "fleet", ["/etc/contrai/profile.toml", "/etc/contrai/accounts.toml"], True
        )


class TestDrainScript:
    """``drain.sh``: the box's one command for stopping a fleet without losing a game."""

    SCRIPT = DEPLOY / "drain.sh"

    def test_it_waits_for_the_line_the_fleet_writes(self):
        # Renamed on one side only, the script would wait out its timeout on
        # every drain and never stop the service.
        fleet = (pathlib.Path(contrai_scraper.__path__[0]) / "fleet.py").read_text(
            encoding="utf-8")
        assert ('"fleet_drained"' in fleet,
                'select(.event == "fleet_drained")' in self.SCRIPT.read_text(
                    encoding="utf-8")) == (True, True)

    def test_it_signals_the_fleet_service_through_both_compose_files(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        assert ("-f deploy/compose.yml -f deploy/compose.fleet.yml" in text,
                '"${COMPOSE[@]}" kill -s USR1 scraper' in text) == (True, True)

    def test_it_follows_the_journal_before_it_signals(self):
        # Started after the signal, the follower could miss a quick drain's
        # last line; started without -n 0, it would match an older drain's.
        text = self.SCRIPT.read_text(encoding="utf-8")
        follow = text.index("journalctl -t contrai-scraper -f -n 0 -o cat")
        assert follow < text.index("kill -s USR1")

    def test_it_is_checked_out_with_unix_line_endings(self):
        # A CRLF shebang names an interpreter that does not exist.
        assert b"\r\n" not in self.SCRIPT.read_bytes()
