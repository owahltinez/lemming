import unittest.mock

import pytest

from lemming import providers


@unittest.mock.patch("subprocess.run")
def test_cloudflare_provider_binary_missing(mock_run):
    mock_run.return_value.returncode = 1
    provider = providers.CloudflareProvider()
    with pytest.raises(RuntimeError, match="cloudflared not found in PATH"):
        provider.start(8999)


@unittest.mock.patch("subprocess.run")
@unittest.mock.patch("subprocess.Popen")
@unittest.mock.patch("time.time")
def test_cloudflare_provider_success(mock_time, mock_popen, mock_run):
    mock_run.return_value.returncode = 0
    mock_time.side_effect = [0, 1, 2]

    mock_process = unittest.mock.MagicMock()
    mock_process.stdout.readline.side_effect = [
        "Starting tunnel...",
        "https://mocked.trycloudflare.com",
        "",
    ]
    mock_popen.return_value = mock_process

    provider = providers.CloudflareProvider()
    url = provider.start(8999)

    assert url == "https://mocked.trycloudflare.com"
    assert provider.process is not None
    provider.stop()
    assert provider.process is None


@unittest.mock.patch("subprocess.run")
def test_tailscale_provider_binary_missing(mock_run):
    mock_run.return_value.returncode = 1
    provider = providers.TailscaleProvider()
    with pytest.raises(RuntimeError, match="tailscale not found in PATH"):
        provider.start(8999)


@unittest.mock.patch("subprocess.run")
def test_tailscale_provider_success(mock_run):
    # Mock which tailscale
    # Mock tailscale serve
    # Mock tailscale funnel
    # Mock tailscale status
    mock_run.side_effect = [
        unittest.mock.MagicMock(returncode=0),  # which
        unittest.mock.MagicMock(returncode=0),  # serve
        unittest.mock.MagicMock(returncode=0),  # funnel
        unittest.mock.MagicMock(
            returncode=0, stdout='{"Self": {"DNSName": "my-node.tail-scale.net."}}'
        ),  # status
        unittest.mock.MagicMock(returncode=0),  # stop: funnel off
        unittest.mock.MagicMock(returncode=0),  # stop: serve reset
    ]

    provider = providers.TailscaleProvider()
    url = provider.start(8999)

    assert url == "https://my-node.tail-scale.net:8999"

    # Test stop
    provider.stop()
    assert mock_run.call_count == 6  # 4 in start + 2 in stop
