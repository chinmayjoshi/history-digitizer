"""Generate comic-panel artwork for stories using a cheap image model on
OpenRouter (default: google/gemini-2.5-flash-image).

Consistency strategy:
  * A global period art-style directive is applied to every panel.
  * Per story, the figures + summary + era are injected as context.
  * Panels after the first attach the FIRST panel's rendered image as a
    visual reference so characters, palette and technique stay consistent
    across the whole story.

Output: web-optimized JPEGs (max ~900px, quality 82) at
    <site_dir>/assets/comic/<slug>_<idx>.jpg
Images already present are skipped (resumable).

Usage:
    python3 tools/comic_scenes.py <book>/stories/<slug>.json \
        [--site-dir docs] [--model google/gemini-2.5-flash-image]
        [--panels 1,3,7] [--width 900] [--size 1024x1024]

Auth: OPENROUTER_API_KEY env, else opencode's auth.json key.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.request

from PIL import Image

API_URL = "https://openrouter.ai/api/v1/chat/completions"

GLOBAL_STYLE = (
    "You are the illustrator of an 18th-century history book, working as a "
    "copperplate engraver. Render the scene as a hand-drawn woodcut engraving: "
    "sepia and dark ink on aged parchment paper, fine cross-hatching and "
    "stippling, dramatic chiaroscuro light, period-accurate costume and "
    "architecture (turbans, robes, war elephants, stone forts, palm and desert "
    "scenery as appropriate). A single coherent wide scene composition, "
    "figures in the foreground and middle distance. IMPORTANT: no text, no "
    "words, no letters, no numbers, no signature, no watermark anywhere in "
    "the image."
)

REFERENCE_LINE = (
    "Match the exact same artistic style, colour palette, line technique and "
    "the appearance of the characters and setting as the attached reference "
    "image, so all panels look like they come from one engraver and characters "
    "stay consistent."
)


def get_key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY")
    if k:
        return k
    return json.load(open(os.path.expanduser("~/.local/share/opencode/auth.json")))["openrouter"]["key"]


def content_with_image(prompt: str, ref_b64: str | None) -> list:
    content = []
    if ref_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{ref_b64}"}})
    content.append({"type": "text", "text": prompt})
    return content


SOFTEN = (
    "This is a dignified historical woodcut for a museum-education edition of "
    "a public-domain 18th-century history of India. Focus on the composition, "
    "costume, architecture and atmosphere. Any military action should be shown "
    "as movement and massed figures, implied rather than graphic: no gore, no "
    "blood, no open wounds, no corpses in the foreground."
)


def gen_image(key: str, model: str, prompt: str, size: str, ref_b64: str | None, attempts: int = 4) -> bytes:
    last = None
    for attempt in range(attempts):
        p = prompt + (f"\n\n{SOFTEN}" if attempt > 0 else "")
        body = {"model": model,
                "messages": [{"role": "user", "content": content_with_image(p, ref_b64)}],
                "size": size}
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/history-digitizer",
                     "X-Title": "history-digitizer"})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:200]}"
            continue
        if data["choices"][0].get("finish_reason") == "content_filter":
            last = "content_filter"
            continue
        msg = data["choices"][0]["message"]
        for src in (msg.get("images"), msg.get("content")):
            if not src:
                continue
            for item in (src if isinstance(src, list) else [src]):
                u = None
                if isinstance(item, dict):
                    u = item.get("image_url")
                    if isinstance(u, dict):
                        u = u.get("url")
                    if not u and item.get("type") == "image_url":
                        u = item["image_url"].get("url")
                elif isinstance(item, str):
                    if item.startswith("data:") or item.startswith("http"):
                        u = item
                if u:
                    return _decode(u)
        last = f"no image parsed: {json.dumps(data)[:200]}"
    raise RuntimeError(f"image generation failed ({last}); panel may be too graphic")


def _decode(u: str) -> bytes:
    if u.startswith("data:"):
        return base64.b64decode(u.split(",", 1)[1])
    return urllib.request.urlopen(u, timeout=120).read()


def fig_line(figures) -> str:
    if not figures:
        return "No named figures on this page; keep any figures period-appropriate and generic."
    return "Named figures in this story: " + ", ".join(figures) + ". Keep their identity and appearance consistent across panels."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("story_json")
    ap.add_argument("--site-dir", default="docs")
    ap.add_argument("--model", default="google/gemini-2.5-flash-image")
    ap.add_argument("--panels", default=None)
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--width", type=int, default=900)
    args = ap.parse_args()

    story = json.load(open(args.story_json))
    slug = story.get("slug") or os.path.splitext(os.path.basename(args.story_json))[0]
    outdir = os.path.join(args.site_dir, "assets", "comic")
    os.makedirs(outdir, exist_ok=True)

    panels = story.get("panels", [])
    idxs = list(range(len(panels)))
    if args.panels:
        idxs = [int(x) - 1 for x in args.panels.split(",")]

    summary = story.get("summary", "")[:1400]
    context = (
        f'"Story: {story.get("title")}"\n'
        f'Setting: {story.get("pages","")}\n'
        f'Figures: {story.get("figures", [])}\n'
        f'Plot for context: {summary}\n'
    )

    key = get_key()
    first_b64 = None
    for i in idxs:
        if i >= len(panels):
            print(f"skip panel {i+1}: out of range"); continue
        scene = panels[i].get("scene", "")
        if not scene:
            continue
        outpath = os.path.join(outdir, f"{slug}_{i+1:02d}.jpg")
        if os.path.exists(outpath):
            # load as reference if it's the first panel
            if i == idxs[0]:
                first_b64 = base64.b64encode(open(outpath, "rb").read()).decode()
            print(f"skip {os.path.basename(outpath)} (exists)"); continue

        prompt = (
            f"{GLOBAL_STYLE}\n\n"
            f"{context}\n"
            f"{fig_line(story.get('figures', []))}\n\n"
            f"PANEL {i+1}: Illustrate this scene:\n{scene}"
        )
        if first_b64:
            prompt += f"\n\n{REFERENCE_LINE}"

        print(f"panel {i+1}: {scene[:54]}…", flush=True)
        try:
            raw = gen_image(key, args.model, prompt, args.size, first_b64)
        except RuntimeError as e:
            print(f"  !! {e}", flush=True)
            continue

        img = Image.open(__import__("io").BytesIO(raw)).convert("RGB")
        if img.width > args.width:
            h = round(img.height * args.width / img.width)
            img = img.resize((args.width, h), Image.LANCZOS)
        img.save(outpath, "JPEG", quality=82)
        print(f"  -> {os.path.basename(outpath)} {img.width}x{img.height}")

        if i == idxs[0]:
            first_b64 = base64.b64encode(open(outpath, "rb").read()).decode()


if __name__ == "__main__":
    main()
