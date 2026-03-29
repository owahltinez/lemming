import fastapi.testclient
from lemming import api


def test_share_token_middleware():
    # Setup test client
    original_token = getattr(api.app.state, "share_token", None)
    try:
        api.app.state.share_token = "secret123"
        # We need a fresh client for each test that modifies app state middleware if it uses the app state
        # but here TestClient is created with api.app
        client = fastapi.testclient.TestClient(api.app)

        # Missing token -> 401
        response = client.get("/api/data")
        assert response.status_code == 401

        # Valid token via query
        response = client.get("/api/data?token=secret123")
        assert response.status_code == 200
        assert "lemming_share_token=secret123" in response.headers.get("set-cookie", "")

        # Valid token via cookie
        client.cookies.set("lemming_share_token", "secret123")
        response = client.get("/api/data")
        assert response.status_code == 200

        # Local bypass via host header
        response = client.get("/api/data", headers={"host": "127.0.0.1:8999"})
        assert response.status_code == 200

        response = client.get("/api/data", headers={"host": "localhost:8999"})
        assert response.status_code == 200
    finally:
        # Restore
        api.app.state.share_token = original_token
