"""CauterRule — Automated standing-rule extraction from agent failures."""

from importlib.metadata import version as _version

try:
    __version__ = _version("cauterule")
except Exception:
    __version__ = "0.3.1"

__all__ = ["__version__"]
