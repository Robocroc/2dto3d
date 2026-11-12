import copy, json, os, tempfile, threading, traceback
from pathlib import Path
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import model, dxf_import, pdf_import

ROOT = Path(__file__).parent
SAVE = ROOT.parent / "project.json"
app = FastAPI(title="Störkanten3D")

EMPTY = {"profiles": {}, "ops": [], "notes": "", "texts": [], "generated": False}
S = {"doc": json.loads(SAVE.read_text()) if SAVE.exists() else copy.deepcopy(EMPTY),
     "undo": [], "redo": [], "chat": [], "upload": None, "job": {"status": "idle"}}


def version():
    """Build-Nr. aus VERSION (bei jedem Commit erhöht); Kurz-Hash aus .git oder (ZIP-Update) aus .build_sha."""
    root = ROOT.parent
    try: build = (root / "VERSION").read_text().strip()
    except Exception: build = "?"
    sha = ""
    try:
        import subprocess
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception: pass
    if not sha:
        try: sha = (root / ".build_sha").read_text().strip()
        except Exception: pass
    return {"build": build, "sha": sha}


VER = version()


def save():
    SAVE.write_text(json.dumps(S["doc"], ensure_ascii=False))


def commit(new):
    S["undo"].append(copy.deepcopy(S["doc"])); S["undo"] = S["undo"][-50:]; S["redo"].clear()
    S["doc"] = new
    save()


def can_undo(): return bool(S["undo"]) and bool(S["undo"][-1].get("generated"))


def state():
    bodies, errors = model.build(S["doc"]) if S["doc"].get("ops") else ({}, [])
    up = S["upload"]
    return {"doc": S["doc"], "meshes": model.meshes(bodies), "errors": errors,
            "can_undo": can_undo(), "can_redo": bool(S["redo"]),
            "ai": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "upload": {"name": up["name"], "kind": up["kind"], "pages": up.get("pages", 1),
                       "scale": up.get("scale"), "scale_note": up.get("scale_note"), "preview": up.get("png") is not None} if up else None,
            "job": S["job"], "version": VER}


@app.get("/api/state")
def get_state(): return state()


@app.post("/api/upload")
async def upload(file: UploadFile, page: int = 0):
    name = file.filename or "zeichnung"
    ext = Path(name).suffix.lower()
    if ext not in (".pdf", ".dxf"):
        raise HTTPException(400, "Nur PDF oder DXF")
    d = Path(tempfile.mkdtemp())
    path = d / f"in{ext}"
    with open(path, "wb") as f:                     # in Stücken schreiben (große Dateien)
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    up = {"name": name, "kind": ext[1:], "path": str(path), "png": None, "pages": 1, "page": page, "scale": None}
    if ext == ".pdf":
        try:
            up["png"], up["pages"] = pdf_import.render(str(path), page)
            import pymupdf
            pg = pymupdf.open(str(path))[page]
            dims = pdf_import.detect_scale_from_dims(pg)       # aus Bemaßungen (Mehrheitsentscheid)
            if dims:
                up["scale"], up["scale_note"] = dims["value"], f"aus {dims['votes']} von {dims['total']} Bemaßungen erkannt"
            else:
                up["scale"] = pdf_import.detect_scale(pg.get_text("words"))
                up["scale_note"] = "aus Schriftfeld gelesen" if up["scale"] else None
        except Exception as e:
            raise HTTPException(400, f"PDF nicht lesbar: {e}")
    S["upload"] = up
    return state()


@app.get("/api/preview.png")
def preview():
    up = S["upload"]
    if not up or not up.get("png"): raise HTTPException(404)
    return Response(up["png"], media_type="image/png")


class GenParams(BaseModel):
    scale: float = 1.0          # Maßstab 1:N  (N)
    height: float = 2800.0      # Gebäudemodus: Wandhöhe
    mode: str = "stoerkante"    # stoerkante | gebaeude
    margin: float = 0.0         # Aufmaß in mm (Störkante)
    notes: str = ""


def fallback_ops(doc, p):
    """Ohne KI: regelbasiertes Modell."""
    profs = doc["profiles"]
    roles = {v["view"]["role"]: k for k, v in profs.items() if v.get("view", {}).get("role")}
    if p.mode == "stoerkante" and len(roles) >= 2:
        op = {"id": "huelle", "type": "view_hull", "name": "Störkontur", "envelope": True, "margin": p.margin}
        op.update({r: roles[r] for r in ("front", "top", "side") if r in roles})
        return [op] + [{"id": f"rund{i}", "type": "round_trim", "target": "huelle", **rf}
                       for i, rf in enumerate(doc.get("rounds") or [], 1)]
    ops = []
    for pid, pr in profs.items():
        if pr.get("view"): continue
        ops.append({"id": f"e_{pid}", "type": "extrude", "profile": pid, "height": p.height,
                    "name": f"{pr['layer']}/{pid}"})
    return ops


def run_generate(p: GenParams):
    try:
        up = S["upload"]
        doc = copy.deepcopy(EMPTY)
        doc.update(notes=p.notes, mode=p.mode, scale=p.scale, margin=p.margin)
        S["job"] = {"status": "running", "msg": "Zeichnung wird ausgelesen …"}
        if up["kind"] == "pdf":
            doc["profiles"], doc["texts"], info = pdf_import.extract(
                up["path"], p.scale, up["page"], rooms=(p.mode == "gebaeude"))
            doc["rounds"] = info.get("rounds", [])
        else:
            doc["profiles"], texts = dxf_import.import_dxf(up["path"]); doc["texts"] = texts
        reply = ""
        if os.environ.get("ANTHROPIC_API_KEY"):
            S["job"] = {"status": "running", "msg": "KI erzeugt das Modell …"}
            try:
                import ai
                doc, reply, _ = ai.generate(doc, up.get("png"))
                if not doc["ops"]: raise RuntimeError("KI hat keine Operationen erzeugt")
            except Exception as e:
                reply = f"KI-Fehler ({e}) – regelbasiertes Modell als Ersatz."
                doc["ops"] = fallback_ops(doc, p)
        else:
            doc["ops"] = fallback_ops(doc, p)
            reply = "Ohne KI (ANTHROPIC_API_KEY fehlt): regelbasiertes Modell aus den erkannten Ansichten (?)."
        doc["generated"] = True
        S["chat"] = []
        commit(doc)
        S["job"] = {"status": "done", "reply": reply}
    except Exception as e:
        traceback.print_exc()
        S["job"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}


@app.post("/api/generate")
def generate(p: GenParams):
    if not S["upload"]: raise HTTPException(400, "Erst eine Zeichnung hochladen")
    if S["job"].get("status") == "running": raise HTTPException(409, "Läuft bereits")
    S["job"] = {"status": "running", "msg": "Start …"}
    threading.Thread(target=run_generate, args=(p,), daemon=True).start()
    return {"job": S["job"]}


@app.get("/api/job")
def job(): return S["job"]


@app.post("/api/reset")
def reset():
    S["doc"] = copy.deepcopy(EMPTY); S["undo"].clear(); S["redo"].clear(); S["chat"] = []
    S["upload"] = None; S["job"] = {"status": "idle"}
    SAVE.unlink(missing_ok=True)
    return state()


class Doc(BaseModel):
    ops: list[dict] | None = None
    notes: str | None = None


@app.post("/api/doc")
def put_doc(d: Doc):
    new = copy.deepcopy(S["doc"])
    if d.ops is not None: new["ops"] = d.ops
    if d.notes is not None: new["notes"] = d.notes
    commit(new)
    return state()


class Msg(BaseModel):
    message: str


@app.post("/api/chat")
def chat(m: Msg):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY nicht gesetzt")
    import ai
    try:
        new, reply, S["chat"] = ai.chat(S["doc"], S["chat"], m.message)
    except Exception as e:
        raise HTTPException(502, f"KI-Fehler: {e}")
    if new["ops"] != S["doc"]["ops"]: commit(new)
    return {**state(), "reply": reply}


@app.post("/api/undo")
def undo():
    if can_undo():
        S["redo"].append(S["doc"]); S["doc"] = S["undo"].pop(); save()
    return state()


@app.post("/api/redo")
def redo():
    if S["redo"]:
        S["undo"].append(S["doc"]); S["doc"] = S["redo"].pop(); save()
    return state()


@app.get("/api/export.stp")
def export():
    bodies, _ = model.build(S["doc"])
    if not bodies: raise HTTPException(400, "Modell leer – erst Modell erzeugen")
    p = Path(tempfile.mkdtemp()) / "modell.stp"
    model.export_step(bodies, str(p))
    return FileResponse(p, filename="modell.stp", media_type="application/step")


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
