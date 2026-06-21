"""
psd_writer.py
A from-scratch, dependency-free writer for the Adobe Photoshop (.psd) binary
file format — RGB, 8-bit, uncompressed (raw) channel data, flat layer stack
(no groups/masks/effects).

No PSD-writing library (e.g. psd-tools' write support) was available offline
in the sandbox this was built in, so this implements the relevant slice of
Adobe's published "Photoshop File Formats Specification" directly with
`struct`. Every field layout below was cross-checked against Pillow's own
PSD *reader* (PIL/PsdImagePlugin.py) so a file written here can be read back
and validated without needing real Photoshop available to test against —
see backend/export.py's self-test in `export_real_psd()`.

Scope/limitations (documented honestly, not hidden):
  - 8-bit RGB only, no CMYK/indexed/16-bit support.
  - Regular layers carry a full-opacity alpha channel (every layer is a fully
    opaque rectangle, no soft/shaped edges) — only the bottommost "Background"
    layer omits alpha, matching Photoshop's own convention for that special
    locked layer type. Hidden layers use the layer "visible" flag, not pixel
    alpha.
  - No real layer masks, blending ranges, or effects (drop shadows etc.) —
    those sections are written as present-but-empty (length 0), which is
    valid per spec.
  - Each layer's pixels are a literal crop of the original image at that
    layer's bounding box (see export.py) — there's no inpainting, so the
    Background layer still contains the original pixels of every element
    sitting "on top" of it. Hiding a layer in Photoshop won't reveal a
    clean background underneath; it'll reveal whatever was on the layer(s)
    below, including the original text/graphic baked into that layer's
    own crop. Real "clean removal" needs generative fill, which is a
    Phase 2/3 item per the PRD roadmap, not this MVP.
"""

import struct


def _pascal_string(name, encoding="latin-1"):
    """Length-prefixed string, padded so the whole field (1-byte length +
    bytes) is a multiple of 4 — matches how Photoshop itself pads layer
    names, even though Pillow's reader doesn't strictly require it."""
    raw = name.encode(encoding, errors="replace")[:255]
    out = bytes([len(raw)]) + raw
    while len(out) % 4 != 0:
        out += b"\x00"
    return out


def _channel_bytes(crop_img):
    """RGB PIL Image -> (R bytes, G bytes, B bytes), each row-major w*h bytes."""
    r, g, b = crop_img.convert("RGB").split()
    return r.tobytes(), g.tobytes(), b.tobytes()


def _layer_record_and_channel_data(name, bbox, crop_img, visible=True, include_alpha=True):
    """bbox = (left, top, right, bottom) in canvas coordinates."""
    left, top, right, bottom = bbox
    w, h = right - left, bottom - top
    r_bytes, g_bytes, b_bytes = _channel_bytes(crop_img)

    channels = [(0, r_bytes), (1, g_bytes), (2, b_bytes)]
    if include_alpha:
        channels.append((-1, b"\xff" * (w * h)))  # fully opaque alpha plane

    record = struct.pack(">iiii", top, left, bottom, right)
    record += struct.pack(">H", len(channels))

    channel_data = b""
    for channel_id, data in channels:
        compressed_len = 2 + len(data)  # 2 bytes for the compression-method field
        record += struct.pack(">hI", channel_id, compressed_len)
        channel_data += struct.pack(">H", 0) + data  # compression = 0 (raw)

    record += b"8BIM" + b"norm"  # blend mode signature + "normal" blend key
    opacity = 255
    clipping = 0
    flags = 0x00 if visible else 0x02  # bit 1 set = hidden
    filler = 0x00
    record += struct.pack(">BBBB", opacity, clipping, flags, filler)

    mask_data = struct.pack(">I", 0)       # no layer mask
    blending_ranges = struct.pack(">I", 0)  # no custom blending ranges
    name_field = _pascal_string(name)
    extra = mask_data + blending_ranges + name_field
    record += struct.pack(">I", len(extra)) + extra

    return record, channel_data


def write_psd(path, width, height, composite_rgb_img, layers):
    """
    layers: ordered BOTTOM-to-TOP (matches Photoshop's layer list semantics
    in the file format — first entry written is the bottommost layer).
    Each item: {"name": str, "bbox": (left, top, right, bottom), "image": PIL Image,
    "visible": bool, "include_alpha": bool (default True; pass False for the
    bottommost Background layer)}
    composite_rgb_img: full-canvas PIL Image used as the flattened preview
    (what non-layer-aware viewers / thumbnails show).
    """
    header = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6
    header += struct.pack(">H", 3)          # channels in composite = 3 (RGB)
    header += struct.pack(">II", height, width)
    header += struct.pack(">HH", 8, 3)      # 8-bit depth, mode 3 = RGB

    color_mode_data = struct.pack(">I", 0)
    image_resources = struct.pack(">I", 0)

    layer_records = b""
    layer_channel_data = b""
    for layer in layers:
        rec, chdata = _layer_record_and_channel_data(
            layer["name"], layer["bbox"], layer["image"],
            layer.get("visible", True), layer.get("include_alpha", True),
        )
        layer_records += rec
        layer_channel_data += chdata

    layer_info_content = struct.pack(">h", len(layers)) + layer_records + layer_channel_data
    if len(layer_info_content) % 2:
        layer_info_content += b"\x00"
    layer_info_section = struct.pack(">I", len(layer_info_content)) + layer_info_content

    global_layer_mask_info = struct.pack(">I", 0)

    layer_and_mask_content = layer_info_section + global_layer_mask_info
    if len(layer_and_mask_content) % 2:
        layer_and_mask_content += b"\x00"
    layer_and_mask_section = struct.pack(">I", len(layer_and_mask_content)) + layer_and_mask_content

    r, g, b = composite_rgb_img.convert("RGB").split()
    image_data_section = struct.pack(">H", 0) + r.tobytes() + g.tobytes() + b.tobytes()

    with open(path, "wb") as f:
        f.write(header)
        f.write(color_mode_data)
        f.write(image_resources)
        f.write(layer_and_mask_section)
        f.write(image_data_section)
