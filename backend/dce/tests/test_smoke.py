from dce import __version__, paths


def test_package_imports() -> None:
    assert __version__
    assert (paths.ROOT / "docs" / "TASK.md").is_file()
