import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from queue import Queue
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


class TestLoginSseStream(unittest.TestCase):
    def test_terminal_error_message_closes_stream(self):
        import app as app_module

        status_queue = Queue()
        payload = json.dumps({"status": "error", "msg": "user closed browser"})
        status_queue.put(payload)

        stream = app_module.sse_stream(status_queue)

        self.assertEqual(next(stream), f"data: {payload}\n\n")
        with patch.object(app_module.time, "sleep", side_effect=AssertionError("stream kept waiting")):
            with self.assertRaises(StopIteration):
                next(stream)

    def _read_login(self, platform, timeout=3):
        import app as app_module

        holder = {}

        def run():
            with patch.object(app_module, "get_platform", return_value=platform):
                resp = app_module.app.test_client().get("/login?type=3&id=sse-proxy-test")
                holder["status"] = resp.status_code
                holder["body"] = resp.get_data(as_text=True)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "登录 SSE 没有在时限内结束")
        return holder["status"], holder["body"]

    def test_login_proxy_error_emits_terminal_500(self):
        class Platform:
            platform_name = "抖音"

            async def login(self, id, status_queue, account_id=None):
                raise RuntimeError("代理连接失败：无法连接代理，连接被拒绝")

        status, body = self._read_login(Platform())
        self.assertEqual(status, 200)
        payload = json.loads(body.removeprefix("data: ").strip())
        self.assertEqual(payload["status"], "500")
        self.assertIn("代理连接失败", payload["msg"])

    def test_login_cancel_still_reports_browser_closed(self):
        class Platform:
            platform_name = "抖音"

            async def login(self, id, status_queue, account_id=None):
                raise asyncio.CancelledError()

        status, body = self._read_login(Platform())
        self.assertEqual(status, 200)
        payload = json.loads(body.removeprefix("data: ").strip())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["msg"], "用户关闭了浏览器")

    def test_login_success_event_closes_stream(self):
        class Platform:
            platform_name = "抖音"

            async def login(self, id, status_queue, account_id=None):
                status_queue.put(json.dumps({"status": "200", "name": "tester"}))

        status, body = self._read_login(Platform())
        self.assertEqual(status, 200)
        payload = json.loads(body.removeprefix("data: ").strip())
        self.assertEqual(payload["status"], "200")
        self.assertEqual(payload["name"], "tester")


if __name__ == "__main__":
    unittest.main()
