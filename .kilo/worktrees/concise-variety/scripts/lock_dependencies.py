"""Pin the installed dependency graph after installing this project's dev dependencies."""

from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ["fastapi", "uvicorn", "python-multipart", "shapely", "pyproj", "pyshp", "defusedxml"]


def lock(roots, filename):
    pending, found = list(roots), {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in found:
            continue
        package = distribution(name)
        found[name] = package.version
        for item in package.requires or []:
            requirement = Requirement(item)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    header = "# Generated from the verified Python 3.14 environment.\n"
    pins = "".join(f"{name}=={version}\n" for name, version in sorted(found.items()))
    (ROOT / filename).write_text(header + pins, encoding="utf-8")


if __name__ == "__main__":
    lock(RUNTIME, "requirements.lock")
    lock(RUNTIME + ["pytest", "httpx", "ruff"], "requirements-dev.lock")
