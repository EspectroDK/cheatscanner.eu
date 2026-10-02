import io

from fastapi.testclient import TestClient
from PIL import Image

from cs2_analyzer.api.app import create_app
from cs2_analyzer.config import Config
from cs2_analyzer.map_images import SOURCE_FILES, fetch, images_dir


def _jpeg(size=(1920, 1080)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (90, 120, 150)).save(out, "JPEG")
    return out.getvalue()


def test_fetch_downloads_resizes_and_skips_existing(tmp_path):
    urls = []

    def download(url):
        urls.append(url)
        if "de_nuke" in url:
            raise OSError("offline")
        return _jpeg()

    dest = tmp_path / "images"
    assert fetch(dest, download=download, log=lambda *_: None) == 1
    assert len(urls) == len(SOURCE_FILES)
    assert all("neustcs/cs2mapsthumbnails" in u for u in urls)
    with Image.open(dest / "de_mirage.jpg") as im:
        assert im.size == (960, 540)
    assert not (dest / "de_nuke.jpg").exists()

    urls.clear()
    assert fetch(dest, download=download, log=lambda *_: None) == 1
    assert len(urls) == 1 and "de_nuke" in urls[0]  # only the missing one again


def test_api_serves_downloaded_map_images(tmp_path):
    config = Config.load(overrides={
        "output": {"dir": str(tmp_path / "out"), "observations_dir": str(tmp_path / "obs")},
        "storage": {"database_url": f"sqlite:///{tmp_path}/test.sqlite"},
        "geometry": {"maps_dir": str(tmp_path / "maps")},
        "api": {"min_free_disk_gb": 0},
    })
    folder = images_dir(tmp_path / "maps")
    folder.mkdir(parents=True)
    (folder / "de_mirage.jpg").write_bytes(_jpeg((960, 540)))
    client = TestClient(create_app(config))
    r = client.get("/maps/de_mirage.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "max-age" in r.headers["cache-control"]
    assert client.get("/maps/de_train.jpg").status_code == 404
    assert client.get("/maps/..%2Fsecret.jpg").status_code == 404
