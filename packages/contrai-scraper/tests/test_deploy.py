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
