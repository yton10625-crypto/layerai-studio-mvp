"""
app.py
Backend entrypoint for the LayerAI Studio MVP skeleton.

PRD section 16 specifies FastAPI. This skeleton uses Flask instead because
the dev sandbox this was built in has no internet access to pip-install
fastapi/uvicorn — Flask ships in the base image and could actually be run
and tested end-to-end here. The route handlers are deliberately thin and
framework-agnostic (all real logic lives in mock_ai.py / export.py /
database.py), so porting routes/*.py to FastAPI routers later is mechanical:
same function bodies, swap @bp.get/@bp.post decorators for @router.get/post
and Flask's request.json for a Pydantic request model.

Run:
    cd backend && python app.py
Serves the API on http://localhost:5050 and the frontend (frontend/) on the
same origin at http://localhost:5050/ — no CORS setup needed for local dev.
"""

import os
from flask import Flask, send_from_directory

from database import init_db
from routes.projects import bp as projects_bp
from routes.analysis import bp as analysis_bp
from routes.layers import bp as layers_bp
from routes.export import bp as export_bp

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = Flask(__name__)
app.register_blueprint(projects_bp)
app.register_blueprint(analysis_bp)
app.register_blueprint(layers_bp)
app.register_blueprint(export_bp)


@app.after_request
def add_cors_headers(resp):
    # permissive CORS for local dev convenience if frontend is served separately
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def frontend_assets(filename):
    full_path = os.path.join(FRONTEND_DIR, filename)
    if os.path.exists(full_path):
        return send_from_directory(FRONTEND_DIR, filename)
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.errorhandler(404)
def not_found(e):
    if "/api/" in str(e):
        return {"error": "not found"}, 404
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5050))
    print(f"LayerAI Studio MVP backend running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
