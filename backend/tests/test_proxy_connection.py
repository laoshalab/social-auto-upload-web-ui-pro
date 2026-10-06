"""代理连通性：失败要快，成功要给出公网 IP，浏览器启动前失败则不开浏览器。"""

import asyncio
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


class _ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "private" in self.path:
            body = b'{"ip":"10.1.2.3"}'
        elif "plain" in self.path:
            body = b"198.51.100.8\n"
        else:
            body = b'{"ip":"203.0.113.10"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _start_proxy():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_hanging_proxy():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]

    def accept_forever():
        sock.settimeout(30)
        held = []
        try:
            while True:
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    break
                held.append(conn)
        finally:
            for conn in held:
                conn.close()
            sock.close()

    threading.Thread(target=accept_forever, daemon=True).start()
    return port


class TestProxyConnection(unittest.TestCase):
    def test_blank_and_non_http_fail_immediately(self):
        from impl.settings import check_proxy_connection

        blank = check_proxy_connection("   ")
        self.assertFalse(blank["ok"])
        self.assertEqual(blank["error"], "未填写代理地址")

        socks = check_proxy_connection("socks5://127.0.0.1:1080")
        self.assertFalse(socks["ok"])
        self.assertEqual(socks["error"], "仅支持 HTTP 代理")

    def test_refused_proxy_hides_credentials_and_is_fast(self):
        from impl.settings import check_proxy_connection

        started = time.monotonic()
        result = check_proxy_connection("http://user:s3cret-pass@127.0.0.1:1")
        elapsed = time.monotonic() - started

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "无法连接代理，连接被拒绝")
        self.assertNotIn("s3cret-pass", result["error"])
        self.assertLess(elapsed, 2)

    def test_hanging_proxy_fails_within_two_timeouts(self):
        from impl.settings import _IP_ENDPOINTS, check_proxy_connection

        self.assertEqual(len(_IP_ENDPOINTS), 2)
        port = _start_hanging_proxy()
        started = time.monotonic()
        result = check_proxy_connection(f"http://127.0.0.1:{port}")
        elapsed = time.monotonic() - started

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "连接代理超时")
        # 默认每个地址 4 秒，两个地址。旧实现 3×8 秒会超过 16 秒。
        self.assertGreaterEqual(elapsed, 6)
        self.assertLess(elapsed, 12)

    def test_http_proxy_returns_public_ip_and_falls_through_private(self):
        from impl.settings import check_proxy_connection

        server = _start_proxy()
        self.addCleanup(server.shutdown)
        proxy = f"http://127.0.0.1:{server.server_address[1]}"

        with patch("impl.settings._IP_ENDPOINTS", ("http://example.test/ip",)):
            ok = check_proxy_connection(proxy)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["ip"], "203.0.113.10")

        with patch(
            "impl.settings._IP_ENDPOINTS",
            ("http://example.test/private", "http://example.test/plain"),
        ):
            fallback = check_proxy_connection(proxy)
        self.assertTrue(fallback["ok"])
        self.assertEqual(fallback["ip"], "198.51.100.8")

        with patch("impl.settings._IP_ENDPOINTS", ("http://example.test/private",)):
            private = check_proxy_connection(proxy)
        self.assertFalse(private["ok"])
        self.assertIn("公网 IP", private["error"])

    def test_launch_skips_check_when_unset_and_blocks_when_down(self):
        from impl import _browser

        with patch("impl.settings.get_proxy_url", return_value=None):
            self.assertIsNone(_browser._proxy_for_launch())

        with patch("impl.settings.get_proxy_url", return_value="http://127.0.0.1:1"):
            with self.assertRaises(RuntimeError) as caught:
                _browser._proxy_for_launch()
        self.assertIn("代理连接失败", str(caught.exception))

    def test_create_browser_does_not_launch_when_proxy_check_fails(self):
        from impl._browser import create_browser, create_browser_sync

        with patch("impl._browser._proxy_for_launch", side_effect=RuntimeError("代理连接失败：无法连接代理，连接被拒绝")):
            with patch("cloakbrowser.launch_async") as launch_async:
                with self.assertRaises(RuntimeError):
                    asyncio.run(create_browser(headless=True))
                launch_async.assert_not_called()

            with patch("cloakbrowser.launch") as launch_sync:
                with self.assertRaises(RuntimeError):
                    create_browser_sync(headless=True)
                launch_sync.assert_not_called()

    def test_create_browser_passes_checked_proxy(self):
        from impl._browser import create_browser

        seen = {}

        async def fake_launch(**kwargs):
            seen.update(kwargs)
            return object()

        with patch("impl._browser._proxy_for_launch", return_value="http://127.0.0.1:8899"):
            with patch("cloakbrowser.launch_async", fake_launch):
                browser = asyncio.run(create_browser(headless=True))
        self.assertIsNotNone(browser)
        self.assertEqual(seen.get("proxy"), "http://127.0.0.1:8899")


if __name__ == "__main__":
    unittest.main()
