import pytest

from cs2_analyzer.config import Config


@pytest.fixture
def config(tmp_path):
    return Config.load(overrides={
        "output": {"dir": str(tmp_path / "out"), "observations_dir": str(tmp_path / "obs")},
        "storage": {"database_url": f"sqlite:///{tmp_path}/test.sqlite"},
        "ingest": {"require_match_access": False, "secret_key_file": str(tmp_path / "secret.key")},
        "api": {"min_free_disk_gb": 0},  # test machines may have little free space
    })
