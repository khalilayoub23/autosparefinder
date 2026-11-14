"""Owner-console approval must load the post's media/link/video_key (2026-11-13: the old loader imported a
function that does not exist in that module inside a bare try/except, so every WhatsApp approval published
text only)."""
import json
import inspect

from agents import owner_console as oc


def test_meta_is_read_from_external_post_ids():
    meta = {"media_url": "https://x/qr.jpg", "link": "https://x/go", "video_key": "thumbs/video/a.mp4", "discord": "1"}
    assert oc.post_publish_meta(meta) == ("https://x/qr.jpg", "https://x/go", "thumbs/video/a.mp4")
    assert oc.post_publish_meta(json.dumps(meta)) == ("https://x/qr.jpg", "https://x/go", "thumbs/video/a.mp4")
    assert oc.post_publish_meta({"image_url": "i", "cta_url": "c"}) == ("i", "c", None)
    assert oc.post_publish_meta(None) == (None, None, None)
    assert oc.post_publish_meta("not json") == (None, None, None)


def test_approval_no_longer_imports_the_missing_helper():
    src = inspect.getsource(oc._approve_and_publish)
    assert "_social_meta" not in src and "post_publish_meta" in src
