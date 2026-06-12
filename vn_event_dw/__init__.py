"""Compatibility shim so the package can run directly from the repo checkout.

This extends the package search path to include ``src/vn_event_dw`` so
commands like ``python -m vn_event_dw.cli`` work before an editable install.
"""

from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]

_src_pkg = Path(__file__).resolve().parent.parent / "src" / "vn_event_dw"
if _src_pkg.exists():
    __path__.append(str(_src_pkg))

