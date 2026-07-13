"""Tunnel providers that expose a local port through a public URL."""

import abc
import json
import re
import subprocess
import time


class TunnelProvider(abc.ABC):
    """Abstract interface for a tunnel exposing a local port publicly."""

    @abc.abstractmethod
    def start(self, local_port: int) -> str:
        """Starts the tunnel and returns the public URL.

        Raises an exception on failure.
        """
        pass

    @abc.abstractmethod
    def stop(self):
        """Tears down the tunnel."""
        pass


class CloudflareProvider(TunnelProvider):
    """Tunnel provider backed by Cloudflare quick tunnels (cloudflared)."""

    def __init__(self):
        """Initializes the provider with no running tunnel process."""
        self.process: subprocess.Popen | None = None

    def start(self, local_port: int) -> str:
        """Starts a Cloudflare quick tunnel to the given local port.

        Args:
            local_port: Local port to expose through the tunnel.

        Returns:
            The public tunnel URL.

        Raises:
            RuntimeError: If cloudflared is missing or no URL is obtained.
        """
        if (
            subprocess.run(
                ["which", "cloudflared"], capture_output=True, check=False
            ).returncode
            != 0
        ):
            raise RuntimeError(
                "cloudflared not found in PATH. Please install it via "
                "'brew install cloudflare/cloudflare/cloudflared' "
                "or visit https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            )

        cmd = [
            "cloudflared",
            "tunnel",
            "--url",
            f"http://127.0.0.1:{local_port}",
        ]
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        url_pattern = re.compile(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)")

        start_time = time.time()
        while time.time() - start_time < 15:
            if self.process.stdout is None:
                break
            line = self.process.stdout.readline()
            if not line:
                # Process might have died
                if self.process.poll() is not None:
                    break
                continue
            match = url_pattern.search(line)
            if (
                match
                and "api.trycloudflare.com" not in match.group(1)
                and "update.trycloudflare.com" not in match.group(1)
            ):
                return match.group(1)

        self.stop()
        raise RuntimeError(
            "Failed to obtain Cloudflare tunnel URL within 15 seconds."
        )

    def stop(self):
        """Terminates the cloudflared process, if one is running."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            if self.process.stdout:
                self.process.stdout.close()
            self.process = None


class TailscaleProvider(TunnelProvider):
    """Tunnel provider backed by Tailscale serve and funnel."""

    def __init__(self):
        """Initializes the provider with no running tunnel process."""
        self.process: subprocess.Popen | None = None

    def start(self, local_port: int) -> str:
        """Exposes the given local port via Tailscale serve and funnel.

        Args:
            local_port: Local port to expose through the tunnel.

        Returns:
            The public URL derived from the node's Tailscale domain.

        Raises:
            RuntimeError: If tailscale is missing or setup fails.
        """
        if (
            subprocess.run(
                ["which", "tailscale"], capture_output=True, check=False
            ).returncode
            != 0
        ):
            raise RuntimeError(
                "tailscale not found in PATH. Please install it from https://tailscale.com/download"
            )

        cmd = [
            "tailscale",
            "serve",
            "https",
            "/",
            f"http://127.0.0.1:{local_port}",
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            # funnel is enabled separately: tailscale funnel 8999
            subprocess.run(
                ["tailscale", "funnel", str(local_port), "on"],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                "Failed to start tailscale serve/funnel: "
                f"{e.stderr.decode() if e.stderr else str(e)}"
            )

        status = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0:
            self.stop()
            raise RuntimeError(
                "Failed to run 'tailscale status'. Is tailscaled running?"
            )

        try:
            data = json.loads(status.stdout)
            domain = data.get("Self", {}).get("DNSName", "").strip(".")
            if not domain:
                raise ValueError("No DNSName found in tailscale status.")
            return f"https://{domain}:{local_port}"
        except Exception as e:
            self.stop()
            raise RuntimeError(f"Failed to determine tailscale domain: {e}")

    def stop(self):
        """Turns off Tailscale funnel and serve for this node."""
        # We don't have a long-running process to kill, but we can turn off
        # funnel and serve. It's cleaner to turn them off.
        subprocess.run(
            ["tailscale", "funnel", "off"], capture_output=True, check=False
        )
        subprocess.run(
            ["tailscale", "serve", "reset"], capture_output=True, check=False
        )
        if self.process:
            self.process.terminate()
            self.process = None
