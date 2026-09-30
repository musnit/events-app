"""Runtime settings, read once from the environment at start-up.

Every setting is an ``EVENTS_*`` environment variable, so the program runs the same way from a
checkout, a Nix package or a systemd unit. State lives only in ``EVENTS_STATE_DIR``; the program
never writes beside its own code.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
HOUR = 60 * 60
DEFAULT_LISTEN = "127.0.0.1:8771"
OFF = ("0", "false", "no", "off")


def _number(env: dict[str, str], name: str, default: float, *, minimum: float = 0) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from None
    if value < minimum:
        raise SystemExit(f"{name} must be at least {minimum:g}, got {raw!r}")
    return value


def _flag(env: dict[str, str], name: str, default: bool = True) -> bool:
    raw = env.get(name, "").strip().lower()
    return default if not raw else raw not in OFF


def _listen(value: str) -> tuple[str, int]:
    """"127.0.0.1:8771", "[::1]:8771" or ":8771" (every interface) -> (host, port)."""
    host, sep, port_text = value.strip().rpartition(":")
    if not sep:
        raise SystemExit(f"EVENTS_LISTEN must be address:port, got {value!r}")
    try:
        port = int(port_text)
    except ValueError:
        raise SystemExit(f"EVENTS_LISTEN must end in a port number, got {value!r}") from None
    if not 0 <= port <= 65535:
        raise SystemExit(f"EVENTS_LISTEN port must be between 0 and 65535, got {port}")
    return host.strip("[]"), port


def _default_state_dir(env: dict[str, str]) -> Path:
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "events"
    if env.get("HOME"):
        return Path(env["HOME"]) / ".local" / "state" / "events"
    raise SystemExit("set EVENTS_STATE_DIR to the directory for the database")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    state_dir: Path
    web_dir: Path
    # v1 kept JSON files next to its server; import them once from here when set.
    legacy_dir: Path | None
    # Luma calendars to follow from configuration: links, slugs or cal- ids.
    luma_calendars: tuple[str, ...]
    agihouse: bool
    # How old a source's last pull may get before the scheduler pulls it again.
    luma_interval: float
    personal_interval: float  # Luma followed list, registrations and iCal feed
    partiful_interval: float
    agihouse_interval: float
    # Pauses between Luma requests. Luma answers bursts with 429s.
    luma_spacing: float
    manual_spacing: float
    sync_enabled: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        host, port = _listen(env.get("EVENTS_LISTEN", "").strip() or DEFAULT_LISTEN)
        legacy = env.get("EVENTS_LEGACY_DIR", "").strip()
        return cls(
            host=host,
            port=port,
            state_dir=Path(env.get("EVENTS_STATE_DIR", "").strip() or _default_state_dir(env)),
            # A checkout serves web/dist beside the package; the Nix package sets its own copy.
            web_dir=Path(env.get("EVENTS_WEB_DIR", "").strip() or PACKAGE_DIR.parent / "web" / "dist"),
            legacy_dir=Path(legacy) if legacy else None,
            luma_calendars=tuple(dict.fromkeys(t for t in re.split(r"[\s,]+", env.get("EVENTS_LUMA_CALENDARS", "")) if t)),
            agihouse=_flag(env, "EVENTS_AGIHOUSE"),
            luma_interval=_number(env, "EVENTS_LUMA_INTERVAL", 4 * HOUR, minimum=60),
            personal_interval=_number(env, "EVENTS_PERSONAL_INTERVAL", 1 * HOUR, minimum=60),
            partiful_interval=_number(env, "EVENTS_PARTIFUL_INTERVAL", 1 * HOUR, minimum=60),
            agihouse_interval=_number(env, "EVENTS_AGIHOUSE_INTERVAL", 2 * HOUR, minimum=60),
            luma_spacing=_number(env, "EVENTS_LUMA_SPACING", 15),
            manual_spacing=_number(env, "EVENTS_MANUAL_SPACING", 2),
            sync_enabled=_flag(env, "EVENTS_SYNC"),
        )
