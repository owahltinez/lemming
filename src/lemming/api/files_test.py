def test_root_redirect(client, git_repo):
    response = client.get("/files", follow_redirects=False)
    assert response.status_code in [302, 307, 301]
    assert response.headers["location"].endswith("/files/")


def test_list_root(client, git_repo):
    # Test template response
    response = client.get("/files/")
    assert response.status_code == 200
    assert "Lemming" in response.text

    # Test API response
    response = client.get("/api/files/")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]
    assert "file1.txt" in names
    assert "dir1/" in names
    assert "ignored.txt" not in names
    assert "node_modules/" not in names

    # Check metadata
    file1 = next(item for item in data["contents"] if item["name"] == "file1.txt")
    assert file1["size"] == 8  # "content1"
    assert "modified" in file1


def test_list_subdir(client, git_repo):
    # Test template response
    response = client.get("/files/dir1")
    assert response.status_code == 200
    assert "Lemming" in response.text

    # Test API response
    response = client.get("/api/files/dir1")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]
    assert "file2.txt" in names


def test_serve_file(client, git_repo):
    response = client.get("/files/file1.txt")
    assert response.status_code == 200
    assert response.text == "content1"
    assert "text/plain" in response.headers["content-type"]


def test_serve_html_as_text(client, git_repo):
    html_file = git_repo / "test.html"
    html_file.write_text("<html><body>hello</body></html>")
    response = client.get("/files/test.html")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert response.text == "<html><body>hello</body></html>"


def test_serve_js_as_text(client, git_repo):
    js_file = git_repo / "test.js"
    js_file.write_text("console.log('hello');")
    response = client.get("/files/test.js")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert response.text == "console.log('hello');"


def test_serve_ignored_file(client, git_repo):
    response = client.get("/files/ignored.txt")
    assert response.status_code == 403
    assert "Forbidden" in response.text


def test_serve_nonexistent_file(client, git_repo):
    response = client.get("/files/nonexistent.txt")
    assert response.status_code == 404


def test_list_non_git_dir(client, non_git_dir):
    """Files should be listed without errors when not in a git repo."""
    response = client.get("/api/files/")
    assert response.status_code == 200
    data = response.json()
    names = [item["name"] for item in data["contents"]]

    # All files should be visible since there's no git to check ignore rules
    assert "file1.txt" in names
    assert "ignored.txt" in names


def test_serve_images_as_images(client, temp_repo):
    png_file = temp_repo / "test.png"
    png_file.write_bytes(b"fake png content")

    response = client.get("/files/test.png")
    assert response.status_code == 200
    assert "image/png" in response.headers["content-type"]


def test_serve_pdf_as_pdf(client, temp_repo):
    pdf_file = temp_repo / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake pdf")

    response = client.get("/files/test.pdf")
    assert response.status_code == 200
    assert "application/pdf" in response.headers["content-type"]


def test_serve_ts_as_text(client, temp_repo):
    # .ts is often misidentified as video/mp2t
    ts_file = temp_repo / "test.ts"
    ts_file.write_text("export const a = 1;")

    response = client.get("/files/test.ts")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_json_as_text(client, temp_repo):
    json_file = temp_repo / "test.json"
    json_file.write_text('{"a": 1}')

    response = client.get("/files/test.json")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_unknown_extension_as_text(client, temp_repo):
    unknown_file = temp_repo / "test.unknown_extension_xyz"
    unknown_file.write_text("some content")

    # In some environments, unknown extension might return octet-stream or None.
    # Our API forces text/plain for anything not explicitly binary.
    response = client.get("/files/test.unknown_extension_xyz")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_no_extension_as_text(client, temp_repo):
    no_ext_file = temp_repo / "Dockerfile"
    no_ext_file.write_text("FROM alpine")

    response = client.get("/files/Dockerfile")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_serve_zip_as_zip(client, temp_repo):
    zip_file = temp_repo / "test.zip"
    zip_file.write_bytes(b"fake zip content")

    response = client.get("/files/test.zip")
    assert response.status_code == 200
    assert "zip" in response.headers["content-type"]


def test_serve_log_page(client, test_tasks):
    # Try to access the log page for task1
    resp = client.get("/tasks/task1/log")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
