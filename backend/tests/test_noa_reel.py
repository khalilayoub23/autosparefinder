"""NOA reel (social/video_gen.build_post_reel) + its publish path (social/registry.dispatch).

Guards the 2026-11-03 findings: SkyReels motion is allowed ONLY for flat parts (a spark plug turned into
another object, an oil filter into a gold mesh), is off by default, every failure still yields a reel, and
TikTok publishes the stored reel — an IMAGE media_url must never be sent to TikTok as video bytes.
"""
import asyncio
import io
import json
import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from social import registry, video_gen  # noqa: E402


def _photo() -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (500, 500), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([120, 200, 380, 300], fill=(30, 30, 40))
    out = io.BytesIO()
    im.save(out, format="JPEG")
    return out.getvalue()


def test_motion_only_for_flat_parts():
    assert video_gen.skyreels_allowed_for("Brake pad set")
    assert video_gen.skyreels_allowed_for("front BRAKE DISC")
    for part in ("Spark plug", "Oil filter", "Shock absorber", "Air filter", "", None):
        assert not video_gen.skyreels_allowed_for(part)


def test_skyreels_is_off_by_default_and_needs_a_token(monkeypatch):
    monkeypatch.delenv("SKYREELS_ENABLED", raising=False)
    monkeypatch.setenv("HF_TOKEN", "x")
    assert not video_gen.skyreels_enabled()
    monkeypatch.setenv("SKYREELS_ENABLED", "1")
    assert video_gen.skyreels_enabled()
    monkeypatch.setenv("HF_TOKEN", " ")
    assert not video_gen.skyreels_enabled()


def test_reel_without_ai_is_15s_vertical_with_audio(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYREELS_ENABLED", raising=False)
    out = str(tmp_path / "reel.mp4")
    info = asyncio.run(video_gen.make_noa_reel(out, _photo(), "רפידות בלם", "Toyota Corolla 2017",
                                               "Brake pad set", "qr_tiktok_w45"))
    assert info == {"path": out, "ai": False}
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", out], capture_output=True, text=True, check=True).stdout)
    assert abs(float(probe["format"]["duration"]) - 15.0) < 0.2
    kinds = {s["codec_type"] for s in probe["streams"]}
    assert kinds == {"video", "audio"}            # platforms reject video with no audio stream
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)


def test_allowed_part_with_failed_motion_still_makes_a_reel(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYREELS_ENABLED", "1")
    monkeypatch.setenv("HF_TOKEN", "x")

    async def _quota():
        return True

    async def _fail(*_a, **_k):
        return None

    monkeypatch.setattr(video_gen, "_skyreels_quota_ok", _quota)
    monkeypatch.setattr(video_gen, "skyreels_clip", _fail)
    out = str(tmp_path / "reel.mp4")
    info = asyncio.run(video_gen.make_noa_reel(out, _photo(), "x", "", "Brake pad set", "qr_tiktok_w1"))
    assert info["ai"] is False and os.path.getsize(out) > 10_000


def test_unsafe_part_never_calls_the_space(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYREELS_ENABLED", "1")
    monkeypatch.setenv("HF_TOKEN", "x")
    called = []

    async def _boom(*_a, **_k):
        called.append(1)
        return b"x"

    monkeypatch.setattr(video_gen, "skyreels_clip", _boom)
    out = str(tmp_path / "reel.mp4")
    asyncio.run(video_gen.make_noa_reel(out, _photo(), "x", "", "Spark plug", "qr_tiktok_w1"))
    assert called == []


def test_build_post_reel_never_raises_without_a_photo():
    assert asyncio.run(video_gen.build_post_reel(None, "x", "", "Brake pad set", "qr")) is None


def _stub_tiktok(monkeypatch, sent):
    async def _publish_video(video_bytes, caption, hashtags, access_token=None):
        sent.append(video_bytes)
        return {"ok": True, "publish_id": "p1", "error": None}

    mod = types.ModuleType("social.tiktok_publisher")
    mod.publish_video = _publish_video
    monkeypatch.setitem(sys.modules, "social.tiktok_publisher", mod)


def test_tiktok_publishes_the_stored_reel(monkeypatch):
    sent = []
    _stub_tiktok(monkeypatch, sent)
    import s3_storage
    monkeypatch.setattr(s3_storage, "get_object", lambda k: (b"REEL-BYTES", "video/mp4"))
    r = asyncio.run(registry.dispatch("tiktok", "caption #a", video_key="thumbs/video/abc.mp4"))
    assert r["ok"] and sent == [b"REEL-BYTES"]


def test_tiktok_ignores_a_key_outside_the_video_prefix(monkeypatch):
    sent = []
    _stub_tiktok(monkeypatch, sent)
    import s3_storage
    monkeypatch.setattr(s3_storage, "get_object", lambda k: (b"SECRET", "video/mp4"))

    async def _gen(out, **_k):
        with open(out, "wb") as f:
            f.write(b"TEMPLATE")
        return out

    monkeypatch.setattr(video_gen, "make_stitch_product_video", _gen)
    asyncio.run(registry.dispatch("tiktok", "caption", video_key="../../etc/passwd"))
    assert sent == [b"TEMPLATE"]


def test_image_media_url_is_not_sent_to_tiktok_as_video(monkeypatch):
    sent = []
    _stub_tiktok(monkeypatch, sent)

    async def _gen(out, **_k):
        with open(out, "wb") as f:
            f.write(b"TEMPLATE")
        return out

    monkeypatch.setattr(video_gen, "make_stitch_product_video", _gen)
    asyncio.run(registry.dispatch("tiktok", "caption",
                                  media_url="https://autosparefinder.co.il/api/v1/thumbnails/thumbs/qr/x.jpg"))
    assert sent == [b"TEMPLATE"]          # the generated video, never the JPEG


def _fake_space(monkeypatch, file_bytes):
    """Serve the Gradio call protocol from an httpx MockTransport; records requested paths."""
    import httpx
    seen = []

    def handler(req: "httpx.Request"):
        seen.append(req.url.path)
        if req.url.path.endswith("/gradio_api/upload"):
            return httpx.Response(200, json=["/tmp/gradio/in.png"])
        if req.url.path.endswith("/gradio_api/call/generate"):
            return httpx.Response(200, json={"event_id": "e1"})
        if "/gradio_api/call/generate/" in req.url.path:
            body = ('event: complete\ndata: [{"video": {"path": "/tmp/gradio/a/out.mp4", '
                    '"url": "https://h.hf.space/gr/gradio_api/file=/tmp/gradio/a/out.mp4"}}, "OK"]\n\n')
            return httpx.Response(200, text=body)
        if "/gradio_api/file=" in req.url.path and "/gr/" not in req.url.path:
            return httpx.Response(200, content=file_bytes)
        return httpx.Response(200, text="<html>Space home page</html>")   # what the /gr/ URL redirects to

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("HF_TOKEN", "t")
    return seen


def test_skyreels_clip_builds_the_file_url_itself_and_checks_for_mp4(monkeypatch):
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    seen = _fake_space(monkeypatch, mp4)
    assert asyncio.run(video_gen.skyreels_clip(b"png", "p")) == mp4
    assert not any("/gr/" in p for p in seen)        # the Space's "url" field (with /gr/) is never used


def test_skyreels_clip_rejects_a_non_mp4_download(monkeypatch):
    _fake_space(monkeypatch, b"<html>Space home page</html>" * 100)
    assert asyncio.run(video_gen.skyreels_clip(b"png", "p")) is None


def test_unusable_motion_clip_falls_back_to_the_all_photo_reel(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYREELS_ENABLED", "1")
    monkeypatch.setenv("HF_TOKEN", "x")

    async def _quota():
        return True

    async def _garbage(*_a, **_k):
        return b"\x00\x00\x00\x18ftypmp42" + b"not a real video" * 10    # passes the sniff, fails ffmpeg

    monkeypatch.setattr(video_gen, "_skyreels_quota_ok", _quota)
    monkeypatch.setattr(video_gen, "skyreels_clip", _garbage)
    out = str(tmp_path / "reel.mp4")
    info = asyncio.run(video_gen.make_noa_reel(out, _photo(), "x", "", "Brake pad set", "qr_tiktok_w1"))
    assert info["ai"] is False and os.path.getsize(out) > 10_000
