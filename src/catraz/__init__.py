from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("catraz")
except PackageNotFoundError:
    __version__ = "unknown"
