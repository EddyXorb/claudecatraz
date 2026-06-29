from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("claudecatraz")
except PackageNotFoundError:
    __version__ = "unknown"
