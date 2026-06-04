from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


def attach_dashboard(app: FastAPI) -> None:
    @app.get("/dashboard/{collection}", response_class=HTMLResponse)
    def dashboard(collection: str, request: Request):
        reviews = request.app.state.index.list_reviews(collection)
        rows = "".join(
            f"<tr><td>{r.id}</td><td>{r.decision_id}</td>"
            f"<td>{r.candidates}</td></tr>" for r in reviews)
        return f"""<!doctype html><html><head><title>llmwiki {collection}</title></head>
<body><h1>llmwiki — {collection}</h1>
<h2>Review queue</h2>
<table border="1"><tr><th>review</th><th>decision</th><th>candidates</th></tr>
{rows or '<tr><td colspan=3>none</td></tr>'}</table>
</body></html>"""
