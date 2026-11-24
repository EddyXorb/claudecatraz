from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("catraz")
except PackageNotFoundError:
    __version__ = "unknown"
