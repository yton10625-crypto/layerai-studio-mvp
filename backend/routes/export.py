import os
from flask import Blueprint, request, jsonify, send_file

from database import get_conn, new_id, now, rows_to_list
import export as export_lib

bp = Blueprint("export", __name__)

EXPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "storage", "exports")


def _record_export(project_id, export_type, file_path):
    conn = get_conn()
    export_id = new_id("exp_")
    conn.execute(
        "INSERT INTO exports (id, project_id, export_type, file_path, created_at) VALUES (?, ?, ?, ?, ?)",
        (export_id, project_id, export_type, file_path, now()),
    )
    conn.commit()
    conn.close()
    return export_id


@bp.post("/api/export/json")
def do_export_json():
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = export_lib.export_json(project_id, EXPORT_DIR)
    if not path:
        return jsonify({"error": "project not found"}), 404
    export_id = _record_export(project_id, "json", path)
    return jsonify({"export_id": export_id, "download_url": f"/api/export/download/{export_id}"})


@bp.post("/api/export/psd")
def do_export_psd():
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = export_lib.export_real_psd(project_id, EXPORT_DIR)
    if not path:
        return jsonify({"error": "project not found"}), 404
    export_id = _record_export(project_id, "psd", path)
    return jsonify({"export_id": export_id, "download_url": f"/api/export/download/{export_id}"})


@bp.get("/api/export/download/<export_id>")
def download_export(export_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "export not found"}), 404
    return send_file(row["file_path"], as_attachment=True)


@bp.get("/api/exports")
def list_exports():
    project_id = request.args.get("project_id")
    conn = get_conn()
    if project_id:
        rows = conn.execute(
            "SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC", (project_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM exports ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))
