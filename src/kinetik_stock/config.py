from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
BRANDS_CONFIG_PATH = REPO_ROOT / "config" / "brands.yaml"


@dataclass
class BrandConfig:
    key: str
    name: str
    portal_url: str
    scraper: str
    username_env: str
    password_env: str
    # Most portals only need username/password, but some (e.g. Norco's LTP
    # Dealer login) also require a dealer/account ID or similar third field.
    # Maps a logical name a scraper can ask for (e.g. "dealer_id") to the
    # .env variable holding it.
    extra_env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @property
    def username(self) -> Optional[str]:
        return os.environ.get(self.username_env)

    @property
    def password(self) -> Optional[str]:
        return os.environ.get(self.password_env)

    def extra(self, key: str) -> Optional[str]:
        env_name = self.extra_env.get(key)
        return os.environ.get(env_name) if env_name else None

    def missing_credential_envs(self) -> list[str]:
        names = [self.username_env, self.password_env, *self.extra_env.values()]
        return [name for name in names if not os.environ.get(name)]

    def credentials_present(self) -> bool:
        return not self.missing_credential_envs()


def _resolve_portal_url(portal_url: str) -> str:
    """Real brands use an http(s) URL as-is. A bare relative path (used by
    the local demo/test fixture) is resolved to a file:// URI so Playwright
    can open it regardless of the current working directory.
    """
    if urlparse(portal_url).scheme:
        return portal_url
    return (REPO_ROOT / portal_url).resolve().as_uri()


def load_brand_configs(path: Path = BRANDS_CONFIG_PATH) -> list[BrandConfig]:
    load_dotenv(REPO_ROOT / ".env", override=False)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    brands = []
    for entry in raw.get("brands", []):
        brands.append(
            BrandConfig(
                key=entry["key"],
                name=entry["name"],
                portal_url=_resolve_portal_url(entry["portal_url"]),
                scraper=entry["scraper"],
                username_env=entry["username_env"],
                password_env=entry["password_env"],
                extra_env=entry.get("extra_env", {}),
                enabled=entry.get("enabled", True),
            )
        )
    return brands
