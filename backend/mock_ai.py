"""
mock_ai.py
Stand-in AI pipeline for PRD section 8 (Image -> Object Detection -> Segmentation
-> OCR -> Layout Analyzer -> Layer Tree Builder).

This sandbox has no internet access, so the real models named in the PRD
(Florence2 for object detection, SAM2 for segmentation, PaddleOCR for OCR)
can't be downloaded. What's substituted, and why it's a reasonable stand-in:

  - OCR:        Tesseract (pytesseract) — a real, locally-installed OCR
                 engine. Text extraction here is GENUINE, not mocked.
  - Detection:  OpenCV contour/edge analysis — finds the largest non-text
                 visual region and treats it as the hero product/background.
                 This is a crude heuristic, not a learned detector.
  - Layout:     Rule-based classifier (position, size, font height, keyword
                 matching) that assigns each detected text block a role:
                 headline / subheadline / price / cta / logo / decoration.

Swap point for production (PRD Phase 1->2): replace `run_pipeline()`'s
internals with calls to hosted Florence2 / SAM2 / PaddleOCR endpoints. The
output contract (the dict shape returned by run_pipeline) should stay the
same so nothing downstream (layer tree builder, API, frontend) needs to
change.
"""

import re
import cv2
import numpy as np
import pytesseract
from PIL import Image

CTA_KEYWORDS = [
    "shop", "buy", "order", "learn more", "sign up", "get started",
    "click", "subscribe", "register", "book now", "add to cart",
    "download", "join", "claim", "discover", "explore", "try free",
    "立即", "马上", "购买", "了解更多", "注册", "下单", "抢购",
]

PRICE_PATTERN = re.compile(
    r"(RM|MYR|\$|USD|¥|￥|€|£)\s?\d[\d,]*\.?\d*|\d+\s?(off|%)", re.IGNORECASE
)


def _ocr_pass(img, psm):
    data = pytesseract.image_to_data(
        img, output_type=pytesseract.Output.DICT, config=f"--psm {psm}"
    )
    lines = {}
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        conf = float(data["conf"][i]) if data["conf"][i] not in ("-1", -1) else -1
        # confidence floor + drop punctuation-only noise tokens (common
        # false-positives from busy/sparse PSM modes on stylized buttons)
        if not text or conf < 45 or not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        if key not in lines:
            lines[key] = {"words": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h, "confs": []}
        ln = lines[key]
        ln["words"].append(text)
        ln["x0"] = min(ln["x0"], x)
        ln["y0"] = min(ln["y0"], y)
        ln["x1"] = max(ln["x1"], x + w)
        ln["y1"] = max(ln["y1"], y + h)
        ln["confs"].append(conf)

    blocks = []
    for ln in lines.values():
        blocks.append({
            "text": " ".join(ln["words"]),
            "x": ln["x0"], "y": ln["y0"],
            "w": ln["x1"] - ln["x0"], "h": ln["y1"] - ln["y0"],
            "confidence": round(sum(ln["confs"]) / len(ln["confs"]) / 100, 3),
        })
    return blocks


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx0, by0, bx1, by1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _ocr_blocks(image_path):
    """Real Tesseract OCR, merging a dense-document pass (PSM 6 — reliable
    for normal paragraph-style text) with a sparse-text pass (PSM 11 — needed
    to pick up isolated, widely-spaced elements typical of ad layouts, like
    a price sitting alone in a corner). Tesseract is the available local
    substitute for the PaddleOCR engine named in the PRD; it's noticeably
    weaker on stylized reversed-out text (white-on-dark buttons), which a
    production PaddleOCR integration should handle better."""
    img = Image.open(image_path).convert("RGB")
    dense = _ocr_pass(img, psm=6)
    sparse = _ocr_pass(img, psm=11)

    merged = list(dense)
    for b in sparse:
        if not any(_iou(b, existing) > 0.3 for existing in merged):
            merged.append(b)
    return merged


def _largest_visual_region(image_path, text_blocks, img_w, img_h):
    """Crude stand-in for SAM2 segmentation: find the largest non-text blob
    via edge density, treat it as the 'hero_product' region."""
    img = cv2.imread(image_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((9, 9), np.uint8), iterations=2)

    # zero-out text regions so they don't get picked up as the hero region
    mask = np.ones_like(edges) * 255
    for b in text_blocks:
        x0, y0 = int(b["x"]), int(b["y"])
        x1, y1 = int(b["x"] + b["w"]), int(b["y"] + b["h"])
        cv2.rectangle(mask, (x0, y0), (x1, y1), 0, -1)
    edges = cv2.bitwise_and(edges, mask)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    best = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(best)
    if area < (img_w * img_h) * 0.02:  # too small to be meaningful
        return None
    x, y, w, h = cv2.boundingRect(best)
    return {"x": x, "y": y, "w": w, "h": h}


def _near_corner(block, img_w, img_h, margin=0.22):
    """True if a block sits within `margin` of any image edge — typical
    logo/brand-mark placement."""
    near_left = block["x"] / img_w < margin
    near_right = (block["x"] + block["w"]) / img_w > (1 - margin)
    near_top = block["y"] / img_h < margin
    near_bottom = (block["y"] + block["h"]) / img_h > (1 - margin)
    return (near_left or near_right) or near_top or near_bottom


def classify_layout(blocks, img_w, img_h):
    """Two-pass global classification — PRD section 9 Layout Analyzer.
    Content-based roles (price, cta) are claimed first since regex/keyword
    matches are reliable regardless of position. Size-based roles (headline,
    subheadline) are then assigned by relative text height among whatever's
    left, so one giant headline can't be mistaken for a logo just because it
    happens to sit near the top of the frame.

    Returns (role_to_block dict, list of leftover blocks classified as 'decoration').
    """
    roles = {}

    for b in blocks:
        if "price" not in roles and PRICE_PATTERN.search(b["text"]):
            roles["price"] = b
    for b in blocks:
        if "cta" not in roles and any(kw.lower() in b["text"].lower() for kw in CTA_KEYWORDS):
            roles["cta"] = b

    claimed = {id(b) for b in roles.values()}
    unclaimed = [b for b in blocks if id(b) not in claimed]

    if unclaimed:
        max_h = max(b["h"] for b in unclaimed)
        logo_candidates = [
            b for b in unclaimed
            if b["h"] <= max_h * 0.65 and len(b["text"]) <= 24 and _near_corner(b, img_w, img_h)
        ]
        if logo_candidates:
            roles["logo"] = min(logo_candidates, key=lambda b: b["h"])

    claimed = {id(b) for b in roles.values()}
    unclaimed = [b for b in unclaimed if id(b) not in claimed]

    unclaimed_sorted = sorted(unclaimed, key=lambda b: -b["h"])
    if unclaimed_sorted:
        roles["headline"] = unclaimed_sorted[0]
    if len(unclaimed_sorted) > 1:
        roles["subheadline"] = unclaimed_sorted[1]

    decoration = unclaimed_sorted[2:]
    return roles, decoration


def run_pipeline(image_path):
    """
    Returns:
      {
        "image_width": int, "image_height": int,
        "ocr_data": [ {text, x, y, w, h, confidence}, ... ],
        "layout_structure": { headline: {...}, subheadline: {...}, price: {...},
                               cta: {...}, logo: {...}, hero_product: {...} },
        "layers": [ {name, role, type, content_text, bbox, z_index, confidence}, ... ]
      }
    """
    with Image.open(image_path) as im:
        img_w, img_h = im.size

    ocr_blocks = _ocr_blocks(image_path)

    role_blocks, decoration_blocks = classify_layout(ocr_blocks, img_w, img_h)
    classified = [{**b, "role": role} for role, b in role_blocks.items()]
    classified += [{**b, "role": "decoration"} for b in decoration_blocks]

    hero = _largest_visual_region(image_path, ocr_blocks, img_w, img_h)

    layout_structure = {
        "headline": {}, "subheadline": {}, "price": {}, "cta": {},
        "logo": {}, "hero_product": {},
    }
    for b in classified:
        if b["role"] in layout_structure and not layout_structure[b["role"]]:
            layout_structure[b["role"]] = {
                "text": b["text"],
                "bbox": {"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"]},
            }
    if hero:
        layout_structure["hero_product"] = {"bbox": hero}

    # ---- Layer Tree Builder (PRD section 10) ----
    layers = []
    z = 0
    layers.append({
        "name": "Background", "role": "background", "type": "image",
        "content_text": None,
        "bbox": {"x": 0, "y": 0, "w": img_w, "h": img_h},
        "z_index": z, "confidence": 1.0,
    })
    z += 1
    if hero:
        layers.append({
            "name": "Hero Product", "role": "hero_product", "type": "image",
            "content_text": None, "bbox": hero, "z_index": z, "confidence": 0.6,
        })
        z += 1

    role_display_names = {
        "logo": "Logo", "headline": "Headline", "subheadline": "Subheadline",
        "price": "Price", "cta": "CTA", "decoration": "Decoration",
    }
    # stable ordering: logo, headline, subheadline, price, cta, then decorations
    role_order = ["logo", "headline", "subheadline", "price", "cta"]
    by_role = {}
    for b in classified:
        by_role.setdefault(b["role"], []).append(b)

    for role in role_order:
        for b in by_role.get(role, []):
            layers.append({
                "name": role_display_names[role],
                "role": role, "type": "text",
                "content_text": b["text"],
                "bbox": {"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"]},
                "z_index": z, "confidence": b["confidence"],
            })
            z += 1

    deco_count = 0
    for b in by_role.get("decoration", []):
        deco_count += 1
        layers.append({
            "name": f"Decoration {deco_count}",
            "role": "decoration", "type": "text",
            "content_text": b["text"],
            "bbox": {"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"]},
            "z_index": z, "confidence": b["confidence"],
        })
        z += 1

    return {
        "image_width": img_w,
        "image_height": img_h,
        "ocr_data": ocr_blocks,
        "layout_structure": layout_structure,
        "layers": layers,
    }


FONT_CANDIDATES = ["Montserrat", "Poppins", "Inter", "Archivo", "Sora"]


def guess_font(block_height_px, image_height_px):
    """PRD section 12 — Font Matching V1: recommend nearest-looking fonts.
    Real implementation would compare glyph shapes; this picks deterministically
    off a hash of the text size so results are stable across reloads."""
    idx = int(block_height_px) % len(FONT_CANDIDATES)
    return FONT_CANDIDATES[idx]
