from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()

    if "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return key, value


def load_dotenv_file(path: Path) -> bool:
    if not path.is_file():
        return False

    loaded = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)
        loaded = True
    return loaded


def _candidate_env_paths() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    for base in (Path.cwd().resolve(), Path(__file__).resolve().parents[2]):
        for directory in (base, *base.parents):
            candidate = (directory / ".env").resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    return candidates


def load_environment_files(paths: Iterable[Path] | None = None) -> list[Path]:
    loaded_paths: list[Path] = []
    for path in paths or _candidate_env_paths():
        if load_dotenv_file(Path(path)):
            loaded_paths.append(Path(path))
    return loaded_paths
