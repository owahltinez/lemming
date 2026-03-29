def test_read_index(client):
    """Test that the root endpoint returns the index.html content."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Verify some content from the index.html file
    assert "Lemming" in response.text


def test_static_files(client):
    """Test that static files are accessible."""
    # Try to access a common static file like mancha.js or index.js
    response = client.get("/static/mancha.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]


def test_filtered_static_files(client):
    """Test that web test files are filtered out from static assets."""
    # Test file that should be hidden
    response = client.get("/static/dashboard.spec.js")
    assert response.status_code == 404

    # Test file that should be hidden
    response = client.get("/static/dashboard.test.js")
    assert response.status_code == 404

    # Normal file should still be accessible
    response = client.get("/static/index.js")
    assert response.status_code == 200
