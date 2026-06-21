import json
from flask import Blueprint, request, jsonify

from database import get_conn, new_id, now, rows_to_list
import mock_ai

bp = Blueprint("analysis", __name__)


@bp.post("/api/analysis/start")
def start_analysis():
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400

    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "project not found"}), 404

    conn.execute("UPDATE projects SET status='analyzing', updated_at=? WHERE id=?", (now(), project_id))
    conn.commit()

    # Synchronous for MVP skeleton. PRD section 16 specifies Redis+Celery for
    # an async job queue — wire that in once analysis runs models heavy
    # enough to need it (this heuristic pipeline runs in well under a second).
    result = mock_ai.run_pipeline(project["original_image_path"])

    conn.execute(
        "UPDATE projects SET status='analyzed', image_width=?, image_height=?, updated_at=? WHERE id=?",
        (result["image_width"], result["image_height"], now(), project_id),
    )

    for ocr in result["ocr_data"]:
        conn.execute(
            "INSERT INTO ocr_data (id, project_id, text, bbox_x, bbox_y, bbox_w, bbox_h, confidence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("ocr_"), project_id, ocr["text"], ocr["x"], ocr["y"], ocr["w"], ocr["h"], ocr["confidence"]),
        )

    conn.execute(
        "INSERT INTO layout_structures (id, project_id, structure_json) VALUES (?, ?, ?)"
        " ON CONFLICT(project_id) DO UPDATE SET structure_json=excluded.structure_json",
        (new_id("layout_"), project_id, json.dumps(result["layout_structure"])),
    )

    for layer in result["layers"]:
        font_guess = mock_ai.guess_font(layer["bbox"]["h"], result["image_height"]) if layer["type"] == "text" else None
        conn.execute(
            "INSERT INTO layers (id, project_id, parent_id, name, role, type, content_text, "
            "font_family_guess, bbox_x, bbox_y, bbox_w, bbox_h, z_index, visible, confidence, "
            "source_image_path, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("layer_"), project_id, None, layer["name"], layer["role"], layer["type"],
                layer["content_text"], font_guess,
                layer["bbox"]["x"], layer["bbox"]["y"], layer["bbox"]["w"], layer["bbox"]["h"],
                layer["z_index"], 1, layer["confidence"], None, now(), now(),
            ),
        )

    conn.commit()
    conn.close()
    return jsonify({"project_id": project_id, "status": "analyzed", "layer_count": len(result["layers"])})


@bp.get("/api/analysis/result")
def analysis_result():
    project_id = request.args.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id query param is required"}), 400

    conn = get_conn()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "project not found"}), 404

    layers = conn.execute(
        "SELECT * FROM layers WHERE project_id=? ORDER BY z_index ASC", (project_id,)
    ).fetchall()
    layout = conn.execute(
        "SELECT * FROM layout_structures WHERE project_id=?", (project_id,)
    ).fetchone()
    conn.close()

    return jsonify({
        "project": dict(project),
        "layers": rows_to_list(layers),
        "layout_structure": json.loads(layout["structure_json"]) if layout else {},
    })
