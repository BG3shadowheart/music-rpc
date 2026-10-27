"""Optional, opt-in cover relay.

Discord does not accept image bytes: it fetches a URL from *its own* servers.
A ``http://127.0.0.1`` URL therefore cannot work, which is why the Windows build
used a Cloudflare Quick Tunnel. On Linux the relay is **off by default** and has
to be enabled explicitly, because it briefly exposes the currently playing
cover to the network.

Two opt-in modes are supported:

* ``cloudflared`` - a random ``*.trycloudflare.com`` hostname, only while
  Music RPC runs (same idea as the Windows build, but never started unless the
  user turns it on).
* ``public_url`` - the user already publishes this machine and supplies the
  base URL (reverse proxy / tunnel they manage themselves).

When the relay is disabled, ``set_image()`` returns ``None`` and the resolver
simply skips that tier and continues with permanent CDN artwork.
"""
from __future__ import annotations

import hashlib
import http.server
import io
import logging
import re
import secrets
import shutil
import subprocess
import threading
import urllib.request

from .constants import RELAY_IMAGE_SIZE, USER_AGENT

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None


class CoverRelay:
    def __init__(self, *, enabled=False, use_cloudflared=False,
                 public_url: str = "", cloudflared_path: str | None = None,
                 port: int = 0):
        self.enabled = bool(enabled)
        self.use_cloudflared = bool(use_cloudflared)
        self.public_url = (public_url or "").rstrip("/") or None
        self._lock = threading.Lock()
        self._image = None
        self._token = None
        self._digest = None
        self._closed = threading.Event()
        self._process = None
        self._server = None
        if not self.enabled:
            return
        if self.public_url and not self.public_url.startswith("https://"):
            log.warning("Relay public URL is not https; refusing to use it")
            self.public_url = None
            self.enabled = False
            return
        try:
            self._start_server(port)
        except Exception as exc:
            log.warning("Cover relay could not start: %s", exc)
            self.enabled = False
            return
        if self.public_url:
            log.info("Cover relay enabled with the configured public URL")
        elif self.use_cloudflared:
            self._start_cloudflared(cloudflared_path)
        else:
            log.warning("Cover relay needs cloudflared or artwork_relay_public_url; "
                        "embedded artwork will not be published")

    # -- HTTP ---------------------------------------------------------------
    def _start_server(self, port):
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                token = self.path.removeprefix("/cover/").split("?", 1)[0].removesuffix(".jpg")
                with owner._lock:
                    image = (owner._image if token and secrets.compare_digest(
                        token, owner._token or "") else None)
                if not image:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(image)))
                self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(image)

            def log_message(self, *_args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True,
                         name="CoverHTTP").start()
        log.info("Cover relay listening on loopback port %s", self._server.server_port)

    # -- tunnel -------------------------------------------------------------
    def _start_cloudflared(self, cloudflared_path):
        binary = cloudflared_path or shutil.which("cloudflared")
        if not binary:
            log.warning("cloudflared is not installed; embedded artwork relay unavailable")
            return
        try:
            self._process = subprocess.Popen(
                [binary, "tunnel", "--no-autoupdate", "--url",
                 f"http://127.0.0.1:{self._server.server_port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
            )
            threading.Thread(target=self._read_tunnel, daemon=True,
                             name="CoverTunnel").start()
            log.info("Temporary cover relay starting (opt-in)")
        except Exception as exc:
            self._process = None
            log.warning("Could not start the cover relay: %s", exc)

    def _read_tunnel(self):
        try:
            for line in self._process.stdout:
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line, re.I)
                if match:
                    self.public_url = match.group(0)
                    log.info("Cover relay is ready")
                    threading.Thread(target=self._warm, args=(self.public_url,),
                                     daemon=True, name="CoverWarm").start()
                    return
                if self._closed.is_set():
                    return
        except Exception as exc:  # pragma: no cover - process plumbing
            if not self._closed.is_set():
                log.warning("Cover relay stopped: %s", exc)

    @staticmethod
    def _warm(url, attempts=2):
        for _ in range(attempts):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=8) as response:
                    response.read(1024)
            except Exception:
                pass

    # -- public API ---------------------------------------------------------
    def set_image(self, data, cache_key=None):
        if not self.enabled or not data or not self.public_url:
            return None
        if Image is None:
            log.debug("Pillow is not installed; cannot re-encode relay artwork")
            return None
        try:
            digest = hashlib.sha256(data).digest()
            with self._lock:
                token = self._token if digest == self._digest else None
            if token is None:
                image = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"),
                                     (RELAY_IMAGE_SIZE, RELAY_IMAGE_SIZE),
                                     method=Image.Resampling.LANCZOS)
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
                payload = out.getvalue()
                with self._lock:
                    self._image = payload
                    self._digest = digest
                    self._token = secrets.token_urlsafe(16)
                    token = self._token
            if not token:
                return None
            url = f"{self.public_url}/cover/{token}.jpg"
            self._warm(url, attempts=1)
            return url
        except Exception as exc:
            log.warning("Cover relay encode failed: %s", exc)
            return None

    def clear(self):
        with self._lock:
            self._image = None
            self._token = None
            self._digest = None

    def close(self):
        self._closed.set()
        self.clear()
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
            except Exception:
                pass
