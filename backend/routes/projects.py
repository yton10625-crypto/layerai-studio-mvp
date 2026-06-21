import os
import uuid
from flask import Blueprint, request, jsonify, send_from_directory

from database import get_conn, new_id, now, rows_to_list

bp = Blueprint("projects", __name__)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "storage", "uploads")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
MAX_BYTES = 20 * 1024 * 1024  # 20MB, per PRD section 6


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


@bp.post("/api/projects/upload")
def upload_project():
    if "file" not in request.files:
        return jsonify({"error": "no file field named 'file' in request"}), 400
    file = request.files["file"]
    if file.filename == "" or not _allowed(file.filename):
        return jsonify({"error": "unsupported file type; allowed: png, jpg, jpeg, webp"}), 400

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > MAX_BYTES:
        return jsonify({"error": "file exceeds 20MB limit"}), 400

    project_id = new_id("proj_")
    ext = file.filename.rsplit(".", 1)[1].lower()
    saved_name = f"{project_id}.{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file.save(saved_path)

    from PIL import Image
    with Image.open(saved_path) as im:
        width, height = im.size

    name = request.form.get("name") or file.filename

    conn = get_conn()
    conn.execute(
        "INSERT INTO projects (id, name, original_image_path, image_width, image_height, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)",
        (project_id, name, saved_path, width, height, now(), now()),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "project_id": project_id,
        "name": name,
        "image_width": width,
        "image_height": height,
        "status": "uploaded",
    }), 201


@bp.get("/api/projects")
def list_projects():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))


@bp.get("/api/projects/<project_id>")
def get_project(project_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "project not found"}), 404
    return jsonify(dict(row))


@bp.get("/api/projects/<project_id>/image")
def get_project_image(project_id):
    conn = get_conn()
    row = conn.execute("SELECT original_image_path FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "project not found"}), 404
    path = row["original_image_path"]
    return send_from_directory(os.path.dirname(path), os.path.basename(path))
