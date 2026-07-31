"""
social/video_gen.py — NOA short-video generator (CPU-only, ffmpeg + PIL).

The server has no GPU, so short promo videos are COMPOSED, not AI-generated: a branded
poster (PIL) → a 15s vertical (1080×1920) MP4 with a slow zoom + fades + silent audio
track (platforms reject video with no audio stream). Used for "Coming Soon" teasers and,
later, part-of-the-week promos.

Public API:
    make_coming_soon_short(out_path, headline="COMING SOON", subline=..., seconds=15) -> path

Notes:
- Text is kept LTR/English + brand for now (PIL has no BiDi/shaping, so Hebrew/Arabic
  would render reversed). Trilingual overlays can be added later with python-bidi.
- Vertical 1080×1920 + ≤60s + #Shorts in the upload title/description => YouTube treats it
  as a Short.
Last Updated: 2026-07-26
"""
from __future__ import annotations

import os
import subprocess
import tempfile

W, H = 1080, 1920
BRAND = "AutoSpareFinder"
SITE = "autosparefinder.co.il"


def _font(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _poster(headline: str, subline: str, cta: str) -> str:
    """Render a branded vertical poster PNG; return its path."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), (11, 17, 32))  # deep navy
    d = ImageDraw.Draw(img)
    # vertical gradient navy -> near black
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(11 + 6 * t), int(17 - 6 * t if t < 1 else 8),
                                       int(32 - 10 * t)))
    accent = (56, 160, 235)   # brand blue
    light = (148, 197, 253)
    # top brand bar
    d.text((W // 2, 250), BRAND, font=_font(64), fill="white", anchor="mm")
    d.line([(W // 2 - 220, 315), (W // 2 + 220, 315)], fill=accent, width=4)
    # big headline (wrap to 2 lines if long)
    hl_font = _font(120)
    words = headline.split()
    if len(headline) > 11 and len(words) > 1:
        mid = len(words) // 2
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    else:
        lines = [headline]
    y = H // 2 - (len(lines) - 1) * 75 - 60
    for ln in lines:
        d.text((W // 2, y), ln, font=hl_font, fill="white", anchor="mm")
        y += 150
    # subline
    d.text((W // 2, y + 40), subline, font=_font(46), fill=light, anchor="mm")
    # accent chip
    d.rounded_rectangle([W // 2 - 300, H - 470, W // 2 + 300, H - 380], radius=45,
                        fill=(20, 30, 52), outline=accent, width=3)
    d.text((W // 2, H - 425), cta, font=_font(40), fill="white", anchor="mm")
    # footer
    d.text((W // 2, H - 250), "🚗  🔧", font=_font(70), fill=light, anchor="mm")
    d.text((W // 2, H - 150), SITE, font=_font(44), fill=accent, anchor="mm")
    p = tempfile.mktemp(suffix=".png")
    img.save(p)
    return p


def make_coming_soon_short(out_path: str, headline: str = "COMING SOON",
                           subline: str = "Find the right car part. Fast.",
                           cta: str = "Search by plate • autosparefinder.co.il",
                           seconds: int = 15) -> str:
    """Compose a branded vertical MP4 (slow zoom + fades + silent audio). Returns out_path."""
    poster = _poster(headline, subline, cta)
    frames = seconds * 30
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"zoompan=z='min(zoom+0.0007,1.18)':d={frames}:s={W}x{H}:fps=30,"
        f"fade=t=in:st=0:d=0.6,fade=t=out:st={seconds-0.9:.2f}:d=0.8,format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", poster,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(seconds), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    finally:
        try:
            os.remove(poster)
        except Exception:
            pass
    return out_path


if __name__ == "__main__":
    out = make_coming_soon_short("/tmp/coming_soon.mp4")
    print("made", out, os.path.getsize(out), "bytes")
