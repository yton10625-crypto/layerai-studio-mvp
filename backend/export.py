"""
export.py
Implements PRD section 11 (PSD Export Strategy) and the LayerAI JSON format
(section 13 Data Model).

PSD export produces a real, openable-in-Photoshop binary .psd file via
psd_writer.py — a from-scratch writer, since no PSD-writing library was
available offline. See psd_writer.py's docstring for exactly what it does
and doesn't support (8-bit RGB, no masks/effects, layers built by cropping
the original image rather than true segmentation).
"""

import os
import json
from PIL import Image

from database import get_conn, rows_to_list
import psd_writer


def _project_layers(project_id):
    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    layers = conn.execute(
        "SELECT * FROM layers WHERE project_id=? ORDER BY z_index ASC", (project_id,)
    ).fetchall()
    layout = conn.execute(
        "SELECT * FROM layout_structures WHERE project_id=?", (project_id,)
    ).fetchone()
    conn.close()
    return dict(project) if project else None, rows_to_list(layers), dict(layout) if layout else None


def build_layerai_json(project_id):
    project, layers, layout = _project_layers(project_id)
    if not project:
        return None
    return {
        "format": "LayerAI JSON",
        "version": "1.0",
        "project": {
            "id": project["id"],
            "name": project["name"],
            "width": project["image_width"],
            "height": project["image_height"],
            "status": project["status"],
        },
        "layout_structure": json.loads(layout["structure_json"]) if layout else {},
        "layers": [
            {
                "id": l["id"],
                "name": l["name"],
                "role": l["role"],
                "type": l["type"],
                "content_text": l["content_text"],
                "font_family_guess": l["font_family_guess"],
                "bbox": {"x": l["bbox_x"], "y": l["bbox_y"], "w": l["bbox_w"], "h": l["bbox_h"]},
                "z_index": l["z_index"],
                "visible": bool(l["visible"]),
                "confidence": l["confidence"],
            }
            for l in layers
        ],
    }


def export_json(project_id, export_dir):
    data = build_layerai_json(project_id)
    if data is None:
        return None
    path = os.path.join(export_dir, f"{project_id}.layerai.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def export_real_psd(project_id, export_dir):
    """Builds a genuine, openable-in-Photoshop .psd file (see psd_writer.py).

    Each layer's pixel content is a literal crop of the original image at
    that layer's bounding box — there's no segmentation/inpainting, so this
    is "layer separation by cropping," not true object extraction. It's
    enough to get independently movable, renameable, hideable layers in
    Photoshop with pixel-accurate content; it does NOT give you a clean
    background when you hide/delete a layer (see psd_writer.py docstring).
    """
    project, layers, _ = _project_layers(project_id)
    if not project:
        return None

    width, height = project["image_width"], project["image_height"]
    src_path = project["original_image_path"]
    original = Image.open(src_path).convert("RGB")
    if original.size != (width, height):
        original = original.resize((width, height))

    psd_layers = []
    # layers are already ordered by z_index ASC (bottom to top) from _project_layers
    for l in layers:
        x, y, w, h = l["bbox_x"], l["bbox_y"], l["bbox_w"], l["bbox_h"]
        if None in (x, y, w, h) or w <= 0 or h <= 0:
            continue
        left = max(0, int(round(x)))
        top = max(0, int(round(y)))
        right = min(width, int(round(x + w)))
        bottom = min(height, int(round(y + h)))
        if right <= left or bottom <= top:
            continue
        crop = original.crop((left, top, right, bottom))
        psd_layers.append({
            "name": l["name"] or l["role"] or "Layer",
            "bbox": (left, top, right, bottom),
            "image": crop,
            "visible": bool(l["visible"]),
        })

    if not psd_layers:
        # guarantee at least one layer so the file isn't degenerate
        psd_layers.append({
            "name": "Background", "bbox": (0, 0, width, height),
            "image": original, "visible": True,
        })

    path = os.path.join(export_dir, f"{project_id}.psd")
    psd_writer.write_psd(path, width, height, original, psd_layers)
    return path
