import io
import time

import pytest

pytest.importorskip("flask")

from kinetik_stock.gui import jobs  # noqa: E402
from kinetik_stock.gui.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "stock_all.csv").write_text("brand,sku\nNorco,A1\nDevinci,B2\n", encoding="utf-8")
    app = create_app("sales@kinetikcycles.com", "pw", output_dir=out)
    c = app.test_client()
    c.post("/login", data={"username": "sales@kinetikcycles.com", "password": "pw"})
    c.app = app
    return c


def test_requires_login(tmp_path):
    c = create_app("sales@kinetikcycles.com", "pw", output_dir=tmp_path).test_client()
    assert c.get("/").status_code == 302
    assert c.get("/api/brands").status_code == 401
    good = {"username": "Sales@KinetikCycles.com", "password": "pw"}
    assert c.post("/login", data={**good, "password": "nope"}).status_code == 401
    assert c.post("/login", data={**good, "username": "other@x.com"}).status_code == 401
    resp = c.post("/login", data=good)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/welcome")
    assert c.get("/").status_code == 200


def test_files_listing_preview_download(client):
    assert [f["name"] for f in client.get("/api/files").json["files"]] == ["stock_all.csv"]
    data = client.get("/api/file/stock_all.csv").json
    assert data["columns"] == ["brand", "sku"] and len(data["rows"]) == 2
    assert client.get("/download/stock_all.csv").status_code == 200
    assert client.get("/api/file/..%2Fsecret.csv").status_code == 404
    assert client.get("/api/file/nope.csv").status_code == 404


def test_job_runs_and_rejects_concurrent(client):
    manager = client.app.extensions["job_manager"]

    def slow(job):
        job.say("working")
        time.sleep(0.3)
        job.rows = 3

    job = manager.start("t", slow)
    with pytest.raises(jobs.Busy):
        manager.start("t2", slow)
    assert client.post("/api/run", json={"brands": ["devinci"]}).status_code == 409
    for _ in range(50):
        if job.status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job.status == "done" and job.log == ["working"]
    assert client.get("/api/job").json["job"]["rows"] == 3


def test_failed_job_reports_error(client):
    manager = client.app.extensions["job_manager"]

    def boom(job):
        raise RuntimeError("bad creds")

    job = manager.start("t", boom)
    for _ in range(50):
        if job.status == "failed":
            break
        time.sleep(0.05)
    assert job.status == "failed" and job.errors == ["bad creds"]


def test_run_validation(client):
    assert client.post("/api/run", json={"brands": ["nope"]}).status_code == 400
    assert client.post("/api/run", json={"brands": []}).status_code == 400
    assert client.post("/api/run", json={}).status_code == 400


def test_in_progress_brands_listed_but_not_runnable(client):
    brands = {b["key"]: b for b in client.get("/api/brands").json["brands"]}
    for key in ("santa_cruz", "specialized", "forbidden"):
        assert brands[key]["coming_soon"] and not brands[key]["credentials_present"]
    assert client.post("/api/run", json={"brands": ["specialized"]}).status_code == 400
    assert client.post("/api/run", json={"brands": ["santa_cruz", "norco"]}).status_code == 400


def _wait(job):
    for _ in range(100):
        if job.status in ("done", "failed"):
            return
        time.sleep(0.05)


def test_each_brand_writes_its_own_file(client, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "_run_stock", lambda key, headless: (calls.append(key), (f"stock_{key}.csv", 2))[1])
    monkeypatch.setattr(jobs, "_run_report", lambda key, headless: (calls.append(key), (f"{key}_report.csv", 5))[1])
    for var in ("TRANSITION_BIKES", "NORCO", "DEVINCI"):
        monkeypatch.setenv(f"{var}_USERNAME", "u")
        monkeypatch.setenv(f"{var}_PASSWORD", "p")
    monkeypatch.setenv("NORCO_DEALER_ID", "d")
    job = client.app.extensions["job_manager"].start(
        "t", jobs.make_brand_work(["transition_bikes", "norco", "devinci"])
    )
    _wait(job)
    assert calls == ["transition_bikes", "norco", "devinci"]
    assert job.outputs == ["stock_transition_bikes.csv", "norco_report.csv", "devinci_report.csv"]
    assert [r["ok"] for r in job.results] == [True, True, True]
    assert job.rows == 12 and job.status == "done"


def test_one_failing_brand_does_not_stop_the_rest(client, monkeypatch):
    def report(key, headless):
        if key == "norco":
            raise RuntimeError("login failed")
        return f"{key}_report.csv", 1

    monkeypatch.setattr(jobs, "_run_report", report)
    for var in ("NORCO", "DEVINCI"):
        monkeypatch.setenv(f"{var}_USERNAME", "u")
        monkeypatch.setenv(f"{var}_PASSWORD", "p")
    monkeypatch.setenv("NORCO_DEALER_ID", "d")
    job = client.app.extensions["job_manager"].start("t", jobs.make_brand_work(["norco", "devinci"]))
    _wait(job)
    assert job.status == "done" and job.outputs == ["devinci_report.csv"]
    norco, devinci = job.results
    assert not norco["ok"] and norco["error"] == "login failed" and devinci["ok"]


def test_missing_credentials_reported_per_brand(client, monkeypatch):
    monkeypatch.delenv("NORCO_USERNAME", raising=False)
    job = client.app.extensions["job_manager"].start("t", jobs.make_brand_work(["norco"]))
    _wait(job)
    assert job.status == "failed" and "Missing credentials" in job.results[0]["error"]


def test_example_brand_is_gone(client):
    assert "example_brand" not in {b["key"] for b in client.get("/api/brands").json["brands"]}


def test_upload_input_validates(client, tmp_path, monkeypatch):
    target = tmp_path / "norco_items.csv"
    target.write_text("System ID,Manufact. SKU,Item\n1,A,x\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "input_csv_path", lambda key: target)

    bad = client.post("/api/inputs/norco", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")})
    assert bad.status_code == 400
    good = client.post(
        "/api/inputs/norco",
        data={"file": (io.BytesIO(b"System ID,Manufact. SKU,Item\n1,A,x\n2,B,y\n"), "x.csv")},
    )
    assert good.status_code == 200
    assert target.read_text().count("\n") == 3
    assert target.with_suffix(".csv.bak").exists()
    assert client.get("/api/inputs").json["inputs"][0]["rows"] == 2


def _logged_in(tmp_path, private):
    c = create_app("sales@kinetikcycles.com", "pw", output_dir=tmp_path, private_dir=private).test_client()
    c.post("/login", data={"username": "sales@kinetikcycles.com", "password": "pw"})
    return c


def test_welcome_page_needs_login_and_works_without_a_photo(tmp_path):
    anon = create_app("sales@kinetikcycles.com", "pw", output_dir=tmp_path, private_dir=tmp_path / "none").test_client()
    assert anon.get("/welcome").status_code == 302
    assert anon.get("/greeting-photo").status_code == 302

    c = _logged_in(tmp_path, tmp_path / "none")
    page = c.get("/welcome").get_data(as_text=True)
    assert "Remember, Mert is watching you." in page and "<img" not in page
    assert c.get("/greeting-photo").status_code == 404


def test_welcome_photo_is_read_from_the_private_folder(tmp_path):
    from PIL import Image

    private = tmp_path / "private"
    private.mkdir()
    Image.new("RGB", (4, 4), "red").save(private / "greeting.png")
    c = _logged_in(tmp_path, private)
    assert "<img" in c.get("/welcome").get_data(as_text=True)
    photo = c.get("/greeting-photo")
    assert photo.status_code == 200 and photo.mimetype == "image/png"


def test_heif_photo_is_converted_to_jpeg(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    pillow_heif.register_heif_opener()
    private = tmp_path / "private"
    private.mkdir()
    Image.new("RGB", (64, 64), "blue").save(private / "greeting.heic", format="HEIF")
    photo = _logged_in(tmp_path, private).get("/greeting-photo")
    assert photo.status_code == 200 and photo.mimetype == "image/jpeg"
    assert photo.data[:2] == b"\xff\xd8"  # JPEG magic bytes


def test_private_folder_is_gitignored():
    import subprocess

    from kinetik_stock.config import REPO_ROOT

    result = subprocess.run(["git", "check-ignore", "private/greeting.heic"], cwd=REPO_ROOT)
    assert result.returncode == 0
