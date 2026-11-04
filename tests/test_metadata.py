"""The version is static in pyproject.toml and in the package. Keep both equal."""

import pathlib

import pytest

import pywebchannel

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_the_package_version_matches_pyproject():
    tomllib = pytest.importorskip("tomllib")

    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == pywebchannel.__version__
