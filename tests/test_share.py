import os
import time
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from lemming.main import cli, parse_timeout
from lemming.api import app
from fastapi.testclient import TestClient

def test_parse_timeout():
    assert parse_timeout("0") == 0.0
    assert parse_timeout("-1h") == 0.0
    assert parse_timeout("8h") == 8 * 3600.0
    assert parse_timeout("30m") == 30 * 60.0
    assert parse_timeout("90s") == 90.0
    assert parse_timeout("invalid") == 0.0


def test_share_token_middleware():
    # Setup test client
    app.state.share_token = "secret123"
    client = TestClient(app)

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
    # The TestClient sends host='testserver', so it defaults to non-local in our middleware if we don't mock it
    # We can fake local host
    response = client.get("/api/data", headers={"host": "127.0.0.1:8999"})
    assert response.status_code == 200
    
    response = client.get("/api/data", headers={"host": "localhost:8999"})
    assert response.status_code == 200


@patch("uvicorn.run")
@patch("lemming.providers.CloudflareProvider")
def test_share_cloudflare_command(mock_cf, mock_uvicorn, tmp_path):
    # Setup mock provider
    mock_provider = MagicMock()
    mock_provider.start.return_value = "https://mock.trycloudflare.com"
    mock_cf.return_value = mock_provider
    
    # We override sleep so monitor thread exits instantly
    with patch("time.sleep", return_value=None), patch("os._exit", side_effect=SystemExit):
        runner = CliRunner()
        result = runner.invoke(cli, ["--tasks-file", str(tmp_path / "tasks.yml"), "share", "--timeout", "0"])
        
        assert result.exit_code == 0
        assert "Initiating public tunnel via Cloudflare" in result.output
        assert "https://mock.trycloudflare.com?token=" in result.output
        mock_provider.start.assert_called_once_with(8999)
        mock_uvicorn.assert_called_once()
        mock_provider.stop.assert_called_once()


@patch("uvicorn.run")
@patch("lemming.providers.TailscaleProvider")
def test_share_tailscale_command(mock_ts, mock_uvicorn, tmp_path):
    mock_provider = MagicMock()
    mock_provider.start.return_value = "https://mock.ts.net"
    mock_ts.return_value = mock_provider
    
    with patch("time.sleep", return_value=None), patch("os._exit", side_effect=SystemExit):
        runner = CliRunner()
        result = runner.invoke(cli, ["--tasks-file", str(tmp_path / "tasks.yml"), "share", "--provider", "tailscale", "--timeout", "0"])
        
        assert result.exit_code == 0
        assert "Initiating public tunnel via Tailscale" in result.output
        assert "https://mock.ts.net?token=" in result.output
        mock_provider.start.assert_called_once_with(8999)
        mock_uvicorn.assert_called_once()
        mock_provider.stop.assert_called_once()

