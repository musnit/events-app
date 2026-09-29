"""Runtime settings, read once from the environment at start-up."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HOUR = 60 * 60


def _number(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from None


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    root: Path
    data_dir: Path
    web_dir: Path
    # How old a source's last pull may get before the scheduler pulls it again.
    luma_refresh: float
    personal_refresh: float  # Luma followed list, registrations and iCal feed
    partiful_refresh: float
    agihouse_refresh: float
    # Pauses between Luma requests. Luma answers bursts with 429s.
    luma_spacing: float
    manual_spacing: float
    # Workers can be switched off for tests and local UI work.
    sync_enabled: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        port = int(_number(env, "PORT", 8771))
        if not 0 <= port <= 65535:
            raise SystemExit(f"PORT must be between 0 and 65535, got {port}")
        root = Path(env.get("LUMACAL_ROOT") or ROOT)
        return cls(
            host=env.get("HOST") or "127.0.0.1",
            port=port,
            root=root,
            data_dir=Path(env.get("LUMACAL_DATA_DIR") or root / "data"),
            web_dir=Path(env.get("LUMACAL_WEB_DIR") or root / "web" / "dist"),
            luma_refresh=_number(env, "REFRESH_SECONDS", 4 * HOUR),
            personal_refresh=_number(env, "PERSONAL_REFRESH_SECONDS", 1 * HOUR),
            partiful_refresh=_number(env, "PARTIFUL_REFRESH_SECONDS", 1 * HOUR),
            agihouse_refresh=_number(env, "AGIHOUSE_REFRESH_SECONDS", 2 * HOUR),
            luma_spacing=_number(env, "CALENDAR_SPACING", 15),
            manual_spacing=_number(env, "MANUAL_SPACING", 2),
            sync_enabled=env.get("LUMACAL_SYNC", "1").strip().lower() not in ("0", "false", "no", "off"),
        )
