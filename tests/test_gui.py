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
    app = create_app("pw", output_dir=out)
    c = app.test_client()
    c.post("/login", data={"password": "pw"})
    c.app = app
    return c


def test_requires_login(tmp_path):
    c = create_app("pw", output_dir=tmp_path).test_client()
    assert c.get("/").status_code == 302
    assert c.get("/api/brands").status_code == 401
    assert c.post("/login", data={"password": "nope"}).status_code == 401
    assert c.post("/login", data={"password": "pw"}).status_code == 302
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
    assert client.post("/api/run", json={"kind": "stock", "brands": ["example_brand"]}).status_code == 409
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
    assert client.post("/api/run", json={"kind": "stock", "brands": ["nope"]}).status_code == 400
    assert client.post("/api/run", json={"kind": "report", "brand": "example_brand"}).status_code == 400
    assert client.post("/api/run", json={"kind": "x"}).status_code == 400


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
