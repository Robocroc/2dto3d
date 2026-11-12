import copy, json, os, tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import model, dxf_import

ROOT = Path(__file__).parent
SAVE = ROOT.parent / "project.json"
app = FastAPI(title="Störkanten3D")

EMPTY = {"profiles": {}, "ops": [], "notes": "", "texts": []}
S = {"doc": json.loads(SAVE.read_text()) if SAVE.exists() else copy.deepcopy(EMPTY),
     "undo": [], "redo": [], "chat": []}


def commit(new):
    S["undo"].append(copy.deepcopy(S["doc"])); S["undo"] = S["undo"][-50:]; S["redo"].clear()
    S["doc"] = new
    SAVE.write_text(json.dumps(new, ensure_ascii=False))


def state():
    bodies, errors = model.build(S["doc"])
    return {"doc": S["doc"], "meshes": model.meshes(bodies), "errors": errors,
            "can_undo": bool(S["undo"]), "can_redo": bool(S["redo"]),
            "ai": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/state")
def get_state(): return state()


@app.post("/api/dxf")
async def upload_dxf(file: UploadFile, height: float = 2800):
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
        f.write(await file.read())
    try:
        profiles, texts = dxf_import.import_dxf(f.name)
    except Exception as e:
        raise HTTPException(400, f"DXF nicht lesbar: {e}")
    new = copy.deepcopy(S["doc"])
    new["profiles"] = profiles
    new["texts"] = texts
    new["ops"] = [{"id": f"e_{pid}", "type": "extrude", "profile": pid, "height": height,
                   "name": f"{p['layer']}/{pid}"} for pid, p in profiles.items()]
    commit(new)
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
    if S["undo"]:
        S["redo"].append(S["doc"]); S["doc"] = S["undo"].pop()
        SAVE.write_text(json.dumps(S["doc"], ensure_ascii=False))
    return state()


@app.post("/api/redo")
def redo():
    if S["redo"]:
        S["undo"].append(S["doc"]); S["doc"] = S["redo"].pop()
        SAVE.write_text(json.dumps(S["doc"], ensure_ascii=False))
    return state()


@app.get("/api/export.stp")
def export():
    bodies, _ = model.build(S["doc"])
    if not bodies: raise HTTPException(400, "Modell leer")
    p = Path(tempfile.mkdtemp()) / "modell.stp"
    model.export_step(bodies, str(p))
    return FileResponse(p, filename="modell.stp", media_type="application/step")


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
