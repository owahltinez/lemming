import shutil
import subprocess
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


def _tailscale_start_mocks():
    """Builds the subprocess results for a successful tailscale start."""
    return [
        unittest.mock.MagicMock(returncode=0),  # which
        unittest.mock.MagicMock(returncode=0, stdout="", stderr=""),  # funnel
        unittest.mock.MagicMock(
            returncode=0,
            stdout='{"Self": {"DNSName": "my-node.tail-scale.net."}}',
        ),  # status
    ]


@unittest.mock.patch("subprocess.run")
def test_tailscale_provider_success(mock_run):
    mock_run.side_effect = _tailscale_start_mocks()

    provider = providers.TailscaleProvider()
    url = provider.start(8999)

    # Funnel terminates TLS on 443, so the public URL carries no local port.
    assert url == "https://my-node.tail-scale.net"


@unittest.mock.patch("subprocess.run")
def test_tailscale_provider_uses_current_cli_syntax(mock_run):
    mock_run.side_effect = _tailscale_start_mocks()

    providers.TailscaleProvider().start(8999)

    # The single backgrounded funnel command replaces the removed
    # 'serve https / <target>' and 'funnel <port> on' pair.
    funnel_cmd = mock_run.call_args_list[1].args[0]
    assert funnel_cmd == [
        "tailscale",
        "funnel",
        "--bg",
        "--yes",
        "--https=443",
        "http://127.0.0.1:8999",
    ]
    issued = [call.args[0] for call in mock_run.call_args_list]
    assert not any("serve" in cmd for cmd in issued)
    assert not any("on" in cmd for cmd in issued)


@unittest.mock.patch("subprocess.run")
def test_tailscale_provider_start_failure_reports_cli_output(mock_run):
    mock_run.side_effect = [
        unittest.mock.MagicMock(returncode=0),  # which
        unittest.mock.MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: the CLI for serve and funnel has changed.",
        ),
    ]

    provider = providers.TailscaleProvider()
    with pytest.raises(RuntimeError) as excinfo:
        provider.start(8999)

    # The failure must name the incompatibility, not just "failed to start".
    message = str(excinfo.value)
    assert "the CLI for serve and funnel has changed" in message
    assert "tailscale funnel --bg" in message


@unittest.mock.patch("subprocess.run")
def test_tailscale_provider_stop_resets_funnel_and_serve(mock_run):
    mock_run.return_value = unittest.mock.MagicMock(returncode=0)

    providers.TailscaleProvider().stop()

    # 'funnel off' errors out when no funnel is configured; reset is idempotent.
    issued = [call.args[0] for call in mock_run.call_args_list]
    assert ["tailscale", "funnel", "reset"] in issued
    assert ["tailscale", "serve", "reset"] in issued


@pytest.mark.skipif(
    shutil.which("tailscale") is None, reason="tailscale CLI not installed"
)
def test_tailscale_cli_still_accepts_our_flags():
    """Guards against another silent break when the tailscale CLI changes.

    The mocked tests above assert our own argv against a stub, so they stay
    green even when the installed CLI rejects every flag we pass. This checks
    the real binary's documented contract without exposing anything publicly.
    """
    usage = subprocess.run(
        ["tailscale", "funnel", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    documented = usage.stdout + usage.stderr

    for flag in ["--bg", "--yes", "--https"]:
        assert flag in documented, (
            f"tailscale funnel no longer documents {flag}"
        )
    assert "reset" in documented
