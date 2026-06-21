import app

app.init_db()
app.app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)
