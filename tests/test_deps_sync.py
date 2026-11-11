"""Vercel installs Python dependencies from pyproject.toml, local dev and CI from requirements.txt: they must match."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm(req: str) -> str:
    return req.strip().lower().replace("_", "-")


def test_pyproject_and_requirements_list_the_same_dependencies():
    pyproject = {norm(d) for d in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]}
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    requirements = {norm(line) for line in lines if line.strip() and not line.lstrip().startswith(("#", "-"))}
    assert pyproject == requirements, (
        f"only in pyproject.toml: {sorted(pyproject - requirements)}; "
        f"only in requirements.txt: {sorted(requirements - pyproject)}"
    )
