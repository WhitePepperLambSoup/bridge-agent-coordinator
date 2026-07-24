"""Bridge core library."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("bridge-agent-coordinator")
except PackageNotFoundError:
    __version__ = "0.1.1"


__all__ = ["__version__"]
