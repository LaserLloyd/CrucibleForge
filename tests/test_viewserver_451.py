"""4.5.1: the read-only results page (`view`, `test --serve`): what it serves, what it refuses, its headers."""
import base64
import hashlib
import http.client
import os
import socket

import pytest

from crucibleforge import config, jail, quicktest, viewserver
from crucibleforge.firstrun import CODES

REPORT = b"<!doctype html><html><head><style>b{}</style></head><body><b>board</b>" \
         b"<script type=\"application/json\" id=\"board-data\">{\"rows\": []}</script><script>var x = 1;</script>" \
         b"</body></html>"


@pytest.fixture
def folder(tmp_path, monkeypatch):
    data = tmp_path / "data"
    res = data / "results"
    res.mkdir(parents=True)
    (data / "models.yaml").write_text("secret: keyname\n")
    (res / "transcripts_m.jsonl").write_text('{"response": "reply text"}\n')
    (data / "spend").mkdir()
    (data / "spend" / "m.jsonl").write_text('{"cost_usd": 0.25}\n{"cost_usd": 0.5}\n')
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    return data


@pytest.fixture
def serve(folder):
    servers = []

    def go(status=None):
        srv = viewserver.start(0, status, host="127.0.0.1")
        servers.append(srv)
        return srv.server_address[1]
    yield go
    for s in servers:
        s.shutdown()
        s.server_close()


def get(port, path, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, dict(r.getheaders()), body


def test_before_the_report_the_running_page_shows_the_run(folder, serve):
    st = viewserver.Status(model="fake/fake-model", label="m", total=40, ledger=str(folder / "spend" / "m.jsonl"))
    st.step = "answering and judging"
    port = serve(st)
    code, head, body = get(port, "/")
    assert code == 200 and b"http-equiv=refresh" in body
    for want in (b"The test is running", b"fake/fake-model", b"answering and judging", b"1 of about 40", b"$0.7500",
                 b"Started", b"Anyone who can reach this port can read these results"):
        assert want in body, want
    assert b"reply text" not in body


def test_once_the_report_exists_it_is_served_with_a_csp_that_lets_only_its_script_run(folder, serve):
    (folder / "results" / "report.html").write_bytes(REPORT)
    (folder / "results" / "report.md").write_text("# board\n")
    port = serve(viewserver.Status(label="m"))
    for path in ("/", "/report.html", "/index.html"):
        code, head, body = get(port, path)
        assert code == 200 and body == REPORT
    csp = head["Content-Security-Policy"]
    want = "'sha256-" + base64.b64encode(hashlib.sha256(b"var x = 1;").digest()).decode() + "'"
    assert f"script-src {want}" in csp and "default-src 'none'" in csp and "unsafe-eval" not in csp
    assert "connect-src" not in csp and "frame-ancestors 'none'" in csp
    assert not any(k.lower().startswith("access-control") for k in head)
    assert head["X-Content-Type-Options"] == "nosniff"
    code, head, body = get(port, "/report.md")
    assert code == 200 and body == b"# board\n" and head["Content-Type"].startswith("text/markdown")


@pytest.mark.parametrize("path", [
    "/models.yaml", "/../models.yaml", "/results/report.html", "/transcripts_m.jsonl", "/spend/m.jsonl",
    "/%2e%2e/models.yaml", "/..%2fmodels.yaml", "//etc/passwd", "/report.html/../../models.yaml",
    "/report.json", "/results/", "/?/../models.yaml", "/.test-complete.json", "/%2e%2e%2f%2e%2e%2fetc%2fpasswd"])
def test_nothing_outside_the_allow_list_is_reachable(folder, serve, path):
    (folder / "results" / "report.html").write_bytes(REPORT)
    port = serve()
    code, _, body = get(port, path)
    if path.startswith("/?"):  # the query is ignored: it is the report itself
        assert code == 200 and body == REPORT
        return
    assert code == 404, path
    assert b"secret" not in body and b"reply text" not in body and b"root:" not in body


def test_a_report_that_is_a_symlink_out_of_the_folder_is_not_followed(folder, serve):
    (folder / "results" / "report.html").symlink_to(folder / "models.yaml")
    port = serve()
    code, _, body = get(port, "/report.html")
    assert code == 404
    code, _, body = get(port, "/")
    assert code == 200 and b"secret" not in body and b"No report in this folder yet" in body


def test_read_only_and_head_has_no_body(folder, serve):
    (folder / "results" / "report.html").write_bytes(REPORT)
    port = serve()
    for m in ("POST", "PUT", "DELETE", "OPTIONS", "PATCH"):
        assert get(port, "/", m)[0] == 405
    code, head, body = get(port, "/", "HEAD")
    assert code == 200 and body == b"" and int(head["Content-Length"]) == len(REPORT)
    assert (folder / "results" / "report.html").read_bytes() == REPORT


def test_a_taken_port_gives_a_plain_message_and_its_own_code(folder, capsys):
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen()
    try:
        port = s.getsockname()[1]
        with pytest.raises(viewserver.PortTaken):
            viewserver.start(port)
        assert viewserver.main(["--port", str(port)]) == CODES["PORT_TAKEN"] == 40
    finally:
        s.close()
    out = capsys.readouterr().out
    assert out.startswith(f"The results page could not open: something already uses port {port}")
    assert "-p 127.0.0.1:<n>:<n>" in out


def test_port_from_env(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert viewserver.port_from_env() == 8377
    monkeypatch.setenv("PORT", "8378")
    assert viewserver.port_from_env() == 8378
    for bad in ("x", "0", "70000"):
        monkeypatch.setenv("PORT", bad)
        assert viewserver.port_from_env() == 8377


def test_test_takes_serve_and_port_and_nothing_listens_without_them():
    a = quicktest.parse(["fake/m", "--serve", "--port", "8390"])
    assert a.serve and a.port == 8390
    assert quicktest.parse(["fake/m", "--open"]).serve
    a = quicktest.parse(["fake/m"])
    assert not a.serve and a.port is None
    assert quicktest.serve_on(None, 7) == 7  # no page: returns at once with the run's code


def test_the_jail_selftest_probes_the_results_page_port(monkeypatch):
    src = jail._HOSTILE
    assert 'out["net_results_page"] = net("127.0.0.1", RESULTS_PORT)' in src
    assert "net_results_page" in open(jail.__file__, encoding="utf-8").read().split("def selftest", 1)[1]


def test_the_docs_show_the_loopback_form_and_the_compose_file_maps_the_port():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = open(os.path.join(root, "docs", "FIRST-RUN-DOCKER.md"), encoding="utf-8").read()
    assert "## Open the results in your browser" in doc
    assert doc.count("-p 127.0.0.1:8377:8377") >= 3 and "Anyone who can reach the port can read the" in doc
    compose = open(os.path.join(root, "deploy", "compose", "compose.yml"), encoding="utf-8").read()
    assert '"${BIND_ADDR:-127.0.0.1}:${VIEW_PORT:-8377}:8377"' in compose
    assert "EXPOSE 8777 8377" in open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
