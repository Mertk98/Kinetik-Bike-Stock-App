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
    assert c.post("/login", data=good).status_code == 302
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
    assert client.post("/api/run", json={"brands": ["example_brand"]}).status_code == 409
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


def test_each_brand_writes_its_own_file(client, tmp_path, monkeypatch):
    from kinetik_stock.models import StockItem, StockStatus

    out = tmp_path / "out"
    monkeypatch.setattr(jobs, "OUTPUT_DIR", out)
    calls = []

    def fake_run_all(only_keys, headless):
        calls.append(only_keys)
        return [StockItem(brand=only_keys[0], sku="S", product_title="T", status=StockStatus.IN_STOCK)], []

    monkeypatch.setattr("kinetik_stock.runner.run_all", fake_run_all)
    monkeypatch.setenv("EXAMPLE_BRAND_USERNAME", "u")
    monkeypatch.setenv("EXAMPLE_BRAND_PASSWORD", "p")
    monkeypatch.setenv("TRANSITION_BIKES_USERNAME", "u")
    monkeypatch.setenv("TRANSITION_BIKES_PASSWORD", "p")
    job = client.app.extensions["job_manager"].start(
        "t", jobs.make_brand_work(["example_brand", "transition_bikes"], True)
    )
    for _ in range(100):
        if job.status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert calls == [["example_brand"], ["transition_bikes"]]
    assert job.outputs == ["stock_example_brand.csv", "stock_transition_bikes.csv"]
    assert {p.name for p in out.glob("*.csv")} == set(job.outputs)


def test_missing_credentials_fail_one_brand_not_all(client, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.delenv("NORCO_USERNAME", raising=False)
    job = client.app.extensions["job_manager"].start("t", jobs.make_brand_work(["norco"], True))
    for _ in range(100):
        if job.status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job.status == "failed" and "Missing credentials" in job.errors[0]


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
