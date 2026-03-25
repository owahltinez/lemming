import pytest
import fastapi.testclient
from lemming import api

client = fastapi.testclient.TestClient(api.app)


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Create a temporary directory and set it as the API root."""
    root = tmp_path / "repo"
    root.mkdir()

    # Mock api.app.state.root
    original_root = api.app.state.root
    api.app.state.root = root

    yield root

    # Restore original root
    api.app.state.root = original_root


def test_serve_images_as_images(temp_repo):
    png_file = temp_repo / "test.png"
    png_file.write_bytes(b"fake png content")

    response = client.get("/files/test.png")
    assert response.status_code == 200
    assert "image/png" in response.headers["content-type"]


def test_serve_pdf_as_pdf(temp_repo):
    pdf_file = temp_repo / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake pdf")

    response = client.get("/files/test.pdf")
    assert response.status_code == 200
    assert "application/pdf" in response.headers["content-type"]


def test_serve_html_as_text(temp_repo):
    html_file = temp_repo / "test.html"
    html_file.write_text("<html><body>hello</body></html>")

    response = client.get("/files/test.html")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_js_as_text(temp_repo):
    js_file = temp_repo / "test.js"
    js_file.write_text("console.log('hello');")

    response = client.get("/files/test.js")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_ts_as_text(temp_repo):
    # .ts is often misidentified as video/mp2t
    ts_file = temp_repo / "test.ts"
    ts_file.write_text("export const a = 1;")

    response = client.get("/files/test.ts")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_json_as_text(temp_repo):
    json_file = temp_repo / "test.json"
    json_file.write_text('{"a": 1}')

    response = client.get("/files/test.json")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_unknown_extension_as_text(temp_repo):
    unknown_file = temp_repo / "test.unknown_extension_xyz"
    unknown_file.write_text("some content")

    response = client.get("/files/test.unknown_extension_xyz")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_no_extension_as_text(temp_repo):
    no_ext_file = temp_repo / "Dockerfile"
    no_ext_file.write_text("FROM alpine")

    response = client.get("/files/Dockerfile")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_zip_as_zip(temp_repo):
    zip_file = temp_repo / "test.zip"
    zip_file.write_bytes(b"fake zip content")

    response = client.get("/files/test.zip")
    assert response.status_code == 200
    # On some systems it might be application/x-zip-compressed or application/zip
    assert "zip" in response.headers["content-type"]
