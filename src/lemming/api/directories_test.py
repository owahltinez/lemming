from lemming import api


def test_list_directories(client, test_tasks):
    """GET /api/directories lists subdirectories under the server root."""
    root = api.app.state.root
    (root / "subproject_a").mkdir(exist_ok=True)
    (root / "subproject_b").mkdir(exist_ok=True)
    (root / ".hidden").mkdir(exist_ok=True)

    response = client.get("/api/directories")
    assert response.status_code == 200
    data = response.json()
    names = [d["name"] for d in data["directories"]]
    assert "subproject_a" in names
    assert "subproject_b" in names
    assert ".hidden" not in names  # hidden dirs excluded


def test_create_directory(client, test_tasks):
    """POST /api/directories creates a new directory."""
    root = api.app.state.root
    response = client.post("/api/directories", json={"name": "new_dir"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "new_dir"
    assert data["path"] == "new_dir"
    assert (root / "new_dir").is_dir()

    # Test creating in a subdirectory
    (root / "parent").mkdir()
    response = client.post("/api/directories", json={"path": "parent", "name": "child"})
    assert response.status_code == 200
    assert (root / "parent" / "child").is_dir()


def test_create_directory_exists(client, test_tasks):
    """POST /api/directories fails if directory already exists."""
    root = api.app.state.root
    (root / "existing").mkdir()
    response = client.post("/api/directories", json={"name": "existing"})
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_create_directory_traversal(client, test_tasks):
    """POST /api/directories rejects path traversal."""
    response = client.post(
        "/api/directories", json={"path": "../../etc", "name": "foo"}
    )
    assert response.status_code == 403

    response = client.post("/api/directories", json={"name": "../outside"})
    assert response.status_code == 403


def test_list_directories_traversal(client, test_tasks):
    """GET /api/directories rejects path traversal."""
    response = client.get("/api/directories", params={"path": "../../etc"})
    assert response.status_code == 403


def test_project_param_traversal_rejected(client, test_tasks):
    """project param rejects path traversal attempts."""
    response = client.get("/api/data", params={"project": "../../etc"})
    assert response.status_code == 403


def test_symlink_traversal_rejected(client, test_tasks):
    """Symlinks pointing outside the root are rejected."""
    import tempfile
    import pathlib
    import shutil

    root = api.app.state.root
    external_dir = pathlib.Path(tempfile.mkdtemp())
    try:
        symlink = root / "sneaky_link"
        symlink.symlink_to(external_dir)

        # /api/directories should not list it (hidden by . filter won't help,
        # but resolve_tasks_file and list_directories should reject traversal)
        response = client.get("/api/data", params={"project": "sneaky_link"})
        assert response.status_code == 403

        response = client.get("/api/directories", params={"path": "sneaky_link"})
        assert response.status_code == 403
    finally:
        symlink.unlink(missing_ok=True)
        shutil.rmtree(external_dir)
