from flask import Blueprint, request, jsonify
import os
import uuid

from database import get_conn, new_id, now, rows_to_list

bp = Blueprint("layers", __name__)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "storage", "uploads")


def _log_modification(conn, project_id, layer_id, field, before, after, action):
    conn.execute(
        "INSERT INTO user_modifications (id, project_id, layer_id, field, before_value, after_value, action, timestamp)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("mod_"), project_id, layer_id, field, str(before), str(after), action, now()),
    )


@bp.post("/api/layers/replace-image")
def replace_image():
    """Multipart upload — swaps the source asset for an image-type layer
    (PRD section 6 Light Editor: 'replace images'). Separate from
    /api/layers/update since file uploads need multipart, not JSON."""
    project_id = request.form.get("project_id")
    layer_id = request.form.get("layer_id")
    if not project_id or not layer_id or "file" not in request.files:
        return jsonify({"error": "project_id, layer_id form fields and a 'file' are required"}), 400

    file = request.files["file"]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "png"
    if ext not in ("png", "jpg", "jpeg", "webp"):
        return jsonify({"error": "unsupported file type"}), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved_name = f"layer_{layer_id}_{uuid.uuid4().hex[:8]}.{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    file.save(saved_path)

    conn = get_conn()
    layer = conn.execute("SELECT * FROM layers WHERE id=?", (layer_id,)).fetchone()
    if not layer:
        conn.close()
        return jsonify({"error": "layer not found"}), 404
    _log_modification(conn, project_id, layer_id, "source_image_path", layer["source_image_path"], saved_path, "replace_image")
    conn.execute("UPDATE layers SET source_image_path=?, updated_at=? WHERE id=?", (saved_path, now(), layer_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "layer_id": layer_id, "image_url": f"/api/layers/{layer_id}/asset"})


@bp.get("/api/layers/<layer_id>/asset")
def layer_asset(layer_id):
    conn = get_conn()
    layer = conn.execute("SELECT source_image_path FROM layers WHERE id=?", (layer_id,)).fetchone()
    conn.close()
    if not layer or not layer["source_image_path"]:
        return jsonify({"error": "no replacement asset for this layer"}), 404
    path = layer["source_image_path"]
    from flask import send_from_directory
    return send_from_directory(os.path.dirname(path), os.path.basename(path))


@bp.put("/api/layers/update")
def update_layer():
    """
    Single endpoint handling every Layer Review Panel + Light Editor action
    from PRD section 6, dispatched by `action`:
      rename | delete | merge | reorder | edit_text | replace_image_url | toggle_visible
    """
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    action = body.get("action")
    if not project_id or not action:
        return jsonify({"error": "project_id and action are required"}), 400

    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "project not found"}), 404

    try:
        if action == "rename":
            layer_id, new_name = body["layer_id"], body["name"]
            layer = conn.execute("SELECT * FROM layers WHERE id=?", (layer_id,)).fetchone()
            if not layer:
                return jsonify({"error": "layer not found"}), 404
            _log_modification(conn, project_id, layer_id, "name", layer["name"], new_name, "rename")
            conn.execute("UPDATE layers SET name=?, updated_at=? WHERE id=?", (new_name, now(), layer_id))

        elif action == "edit_text":
            layer_id, new_text = body["layer_id"], body["content_text"]
            layer = conn.execute("SELECT * FROM layers WHERE id=?", (layer_id,)).fetchone()
            if not layer:
                return jsonify({"error": "layer not found"}), 404
            _log_modification(conn, project_id, layer_id, "content_text", layer["content_text"], new_text, "edit_text")
            conn.execute("UPDATE layers SET content_text=?, updated_at=? WHERE id=?", (new_text, now(), layer_id))

        elif action == "delete":
            layer_id = body["layer_id"]
            layer = conn.execute("SELECT * FROM layers WHERE id=?", (layer_id,)).fetchone()
            if not layer:
                return jsonify({"error": "layer not found"}), 404
            _log_modification(conn, project_id, layer_id, "deleted", False, True, "delete")
            conn.execute("DELETE FROM layers WHERE id=?", (layer_id,))

        elif action == "toggle_visible":
            layer_id = body["layer_id"]
            layer = conn.execute("SELECT * FROM layers WHERE id=?", (layer_id,)).fetchone()
            if not layer:
                return jsonify({"error": "layer not found"}), 404
            new_val = 0 if layer["visible"] else 1
            _log_modification(conn, project_id, layer_id, "visible", layer["visible"], new_val, "toggle_visible")
            conn.execute("UPDATE layers SET visible=?, updated_at=? WHERE id=?", (new_val, now(), layer_id))

        elif action == "merge":
            layer_ids = body["layer_ids"]  # list, 2+
            merged_name = body.get("name", "Merged Layer")
            if len(layer_ids) < 2:
                return jsonify({"error": "merge requires at least 2 layer_ids"}), 400
            layers = conn.execute(
                f"SELECT * FROM layers WHERE id IN ({','.join('?' * len(layer_ids))})", layer_ids
            ).fetchall()
            if len(layers) != len(layer_ids):
                return jsonify({"error": "one or more layers not found"}), 404
            xs = [l["bbox_x"] for l in layers]
            ys = [l["bbox_y"] for l in layers]
            x1s = [l["bbox_x"] + l["bbox_w"] for l in layers]
            y1s = [l["bbox_y"] + l["bbox_h"] for l in layers]
            merged_bbox = (min(xs), min(ys), max(x1s) - min(xs), max(y1s) - min(ys))
            merged_text = " ".join(l["content_text"] for l in layers if l["content_text"])
            min_z = min(l["z_index"] for l in layers)
            new_layer_id = new_id("layer_")

            _log_modification(conn, project_id, new_layer_id, "merge",
                               [l["name"] for l in layers], merged_name, "merge")
            conn.execute(
                "INSERT INTO layers (id, project_id, parent_id, name, role, type, content_text, "
                "font_family_guess, bbox_x, bbox_y, bbox_w, bbox_h, z_index, visible, confidence, "
                "source_image_path, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_layer_id, project_id, None, merged_name, layers[0]["role"], layers[0]["type"],
                 merged_text or None, layers[0]["font_family_guess"],
                 merged_bbox[0], merged_bbox[1], merged_bbox[2], merged_bbox[3],
                 min_z, 1, None, None, now(), now()),
            )
            conn.execute(f"DELETE FROM layers WHERE id IN ({','.join('?' * len(layer_ids))})", layer_ids)

        elif action == "reorder":
            ordered_ids = body["ordered_layer_ids"]  # full list, new top-to-bottom order
            for idx, layer_id in enumerate(reversed(ordered_ids)):
                conn.execute("UPDATE layers SET z_index=?, updated_at=? WHERE id=?", (idx, now(), layer_id))
            _log_modification(conn, project_id, None, "z_index_order", None, ordered_ids, "reorder")

        else:
            conn.close()
            return jsonify({"error": f"unknown action '{action}'"}), 400

        conn.commit()
    except KeyError as e:
        conn.close()
        return jsonify({"error": f"missing required field: {e}"}), 400
    finally:
        conn.close()

    return jsonify({"status": "ok", "action": action})


@bp.get("/api/layers/<project_id>/history")
def modification_history(project_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM user_modifications WHERE project_id=? ORDER BY timestamp DESC", (project_id,)
    ).fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))
