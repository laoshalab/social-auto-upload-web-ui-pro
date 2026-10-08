"""新版投稿页：文件写在 bcc-upload-wrapper，按钮带 no-events，点击会被 upload-area 截走。"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

PAGE = """<!DOCTYPE html>
<html><body>
<style>
  .upload-btn.no-events { pointer-events: none; }
  .upload-area { width: 480px; height: 280px; padding: 40px; }
</style>
<div class="upload-area" id="area">
  <div class="upload-btn no-events">上传视频</div>
  <p id="hint">点击上传或将视频拖拽到此区域</p>
  <input id="real" type="file" accept="video/mp4" style="display:none">
</div>
<input id="decoy" type="file" accept="video/mp4">
<script>
  document.getElementById("area").addEventListener("click", () => {
    document.getElementById("real").click();
  });
  document.getElementById("real").addEventListener("change", () => {
    document.querySelector(".upload-btn").remove();
    document.getElementById("hint").remove();
    document.body.dataset.picked = document.getElementById("real").files[0].name;
  });
  document.getElementById("decoy").addEventListener("change", () => {
    document.getElementById("hint").remove();
    document.body.dataset.decoy = "1";
  });
</script>
</body></html>
"""

# 与线上一致：file input 在 upload-area 外面，另有一个 name=buploader 的干扰输入。
PAGE_WRAPPER = """<!DOCTYPE html>
<html><body>
<style>
  .upload-btn.no-events, .upload-text.no-events { pointer-events: none; }
  .upload-area { width: 480px; height: 280px; }
</style>
<div class="bcc-upload-wrapper">
  <div class="upload-area">
    <div class="upload-text no-events" id="hint">点击上传或将视频拖拽到此区域</div>
    <div class="upload-btn no-events">上传视频</div>
  </div>
  <input id="real" type="file" accept="video/mp4" style="display:none">
</div>
<input id="decoy" name="buploader" type="file" accept="video/mp4">
<script>
  document.getElementById("real").addEventListener("change", () => {
    document.querySelector(".upload-btn").style.display = "none";
    document.body.dataset.picked = document.getElementById("real").files[0].name;
  });
  document.getElementById("decoy").addEventListener("change", () => {
    document.getElementById("hint").remove();
    document.body.dataset.decoy = "1";
  });
</script>
</body></html>
"""


def _run(coro):
    return asyncio.run(coro)


def _temp_video():
    fd, name = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    path = Path(name)
    path.write_bytes(b"fake")
    return path


async def _open_page(html: str):
    from cloakbrowser import launch_async

    fd, name = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    path = Path(name)
    path.write_text(html, encoding="utf-8")
    browser = await launch_async(headless=True)
    page = await browser.new_page()
    await page.goto(path.as_uri(), wait_until="domcontentloaded", timeout=15000)
    return browser, page, path


class TestBilibiliUploadClick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cloakbrowser  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("缺少 cloakbrowser，跳过浏览器上传测试")

    def test_click_hits_upload_area_not_the_no_events_button(self):
        async def scene():
            from impl.bilibili.platform import BilibiliPlatform

            video = _temp_video()
            browser, page, html = await _open_page(PAGE)
            try:
                await BilibiliPlatform._choose_via_upload_button(page, str(video))
                self.assertTrue(await BilibiliPlatform._left_upload_entry(page))
                picked = await page.evaluate("() => document.body.dataset.picked")
                decoy = await page.evaluate("() => document.body.dataset.decoy || ''")
            finally:
                await browser.close()
                html.unlink(missing_ok=True)
                video.unlink(missing_ok=True)
            return picked, decoy

        picked, decoy = _run(scene())
        self.assertTrue(picked.endswith(".mp4"))
        self.assertEqual(decoy, "")

    def test_file_input_writes_area_not_decoy(self):
        async def scene():
            from impl.bilibili.platform import BilibiliPlatform

            video = _temp_video()
            browser, page, html = await _open_page(PAGE)
            try:
                await BilibiliPlatform._set_video_file_input(page, str(video))
                left = await BilibiliPlatform._left_upload_entry(page)
                picked = await page.evaluate("() => document.body.dataset.picked || ''")
                decoy = await page.evaluate("() => document.body.dataset.decoy || ''")
            finally:
                await browser.close()
                html.unlink(missing_ok=True)
                video.unlink(missing_ok=True)
            return left, picked, decoy

        left, picked, decoy = _run(scene())
        self.assertTrue(left)
        self.assertTrue(picked.endswith(".mp4"))
        self.assertEqual(decoy, "")

    def test_button_still_visible_is_not_started(self):
        async def scene():
            from impl.bilibili.platform import BilibiliPlatform

            browser, page, html = await _open_page(PAGE)
            try:
                await page.evaluate("() => document.getElementById('hint').remove()")
                return await BilibiliPlatform._upload_entry_still_open(page)
            finally:
                await browser.close()
                html.unlink(missing_ok=True)

        self.assertTrue(_run(scene()))

    def test_hidden_button_counts_as_left_even_if_hint_remains(self):
        async def scene():
            from impl.bilibili.platform import BilibiliPlatform

            browser, page, html = await _open_page(PAGE)
            try:
                await page.evaluate(
                    """() => {
                      document.querySelector('.upload-btn').style.display = 'none';
                    }"""
                )
                return await BilibiliPlatform._upload_entry_still_open(page)
            finally:
                await browser.close()
                html.unlink(missing_ok=True)

        self.assertFalse(_run(scene()))

    def test_set_wrapper_file_input_starts_upload(self):
        async def scene():
            from impl.bilibili.platform import BilibiliPlatform

            video = _temp_video()
            browser, page, html = await _open_page(PAGE_WRAPPER)
            try:
                wrote = await BilibiliPlatform._set_wrapper_file_input(page, str(video))
                still = await BilibiliPlatform._upload_entry_still_open(page)
                picked = await page.evaluate("() => document.body.dataset.picked || ''")
                decoy = await page.evaluate("() => document.body.dataset.decoy || ''")
            finally:
                await browser.close()
                html.unlink(missing_ok=True)
                video.unlink(missing_ok=True)
            return wrote, still, picked, decoy

        wrote, still, picked, decoy = _run(scene())
        self.assertTrue(wrote)
        self.assertFalse(still)
        self.assertTrue(picked.endswith(".mp4"))
        self.assertEqual(decoy, "")

    def test_upload_video_file_prefers_wrapper_over_decoy(self):
        async def scene():
            from impl.bilibili.platform import BilibiliPlatform

            video = _temp_video()
            browser, page, html = await _open_page(PAGE_WRAPPER)
            try:
                await BilibiliPlatform._upload_video_file(page, str(video))
                still = await BilibiliPlatform._upload_entry_still_open(page)
                picked = await page.evaluate("() => document.body.dataset.picked || ''")
                decoy = await page.evaluate("() => document.body.dataset.decoy || ''")
            finally:
                await browser.close()
                html.unlink(missing_ok=True)
                video.unlink(missing_ok=True)
            return still, picked, decoy

        still, picked, decoy = _run(scene())
        self.assertFalse(still)
        self.assertTrue(picked.endswith(".mp4"))
        self.assertEqual(decoy, "")


if __name__ == "__main__":
    unittest.main()
