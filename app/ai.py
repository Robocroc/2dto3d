"""KI-Schnittstelle: Prosa -> Modelloperationen (Claude Tool-Use)."""
import copy, json, os
import anthropic
import model

MODEL = os.environ.get("STK_MODEL", "claude-sonnet-5-5")

SYSTEM = """Du bist der Modellierassistent eines CAD-Tools für Störkantenmodelle
(Hüllkörper/Kollisionsbereiche) und Gebäudemodelle. Einheiten: mm. Koordinaten: X/Y = Grundriss, Z = Höhe.
Das Modell ist eine geordnete Operationsliste. Du änderst es NUR über die Tools upsert_op und delete_op.
Operationen (Feld "type"):
- extrude {profile|points, height, z=0}  Profil aus DXF (id aus 'profiles') oder eigene Punkte [[x,y],..]
- box {x,y,z,dx,dy,dz}   cylinder {x,y,z,r,h}
- cut_box {target,x,y,z,dx,dy,dz}   cut_cylinder {target,x,y,z,r,h}   (Öffnungen, Aussparungen)
- cut_extrude {target,profile|points,height,z=0}  Profil ausschneiden (z. B. Innenkontur einer Wand)
- move {target,dx,dy,dz}
- offset {target,distance}  Aufmaß-Hüllkörper um einen Körper (Störkante), envelope=true
- bbox_envelope {target?,margin}  Hüllquader um Körper oder alles
- delete {target}
Gemeinsame Felder: id (eindeutig, kurz), name, color (#rrggbb, optional), envelope (bool: als transparente Störkontur darstellen).
Regeln: Bestehende ids beim Ändern wiederverwenden (upsert). Nach jedem Tool-Aufruf bekommst du
Baufehler zurück – behebe sie. Maße nie raten: fehlen sie, frage kurz nach statt zu erfinden;
Annahmen im Antworttext mit (?) kennzeichnen. Antworte kurz, auf Deutsch."""

TOOLS = [
    {"name": "upsert_op", "description": "Operation anlegen oder (bei gleicher id) ersetzen.",
     "input_schema": {"type": "object", "required": ["op"], "properties": {
         "op": {"type": "object", "required": ["id", "type"],
                "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": model.OP_TYPES}},
                "additionalProperties": True},
         "after": {"type": "string", "description": "id, hinter der eingefügt wird (nur bei neuer Op)"}}}},
    {"name": "delete_op", "description": "Operation aus der Liste entfernen.",
     "input_schema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
]


def _context(doc):
    profs = []
    for pid, p in doc["profiles"].items():
        xs, ys = [q[0] for q in p["points"]], [q[1] for q in p["points"]]
        profs.append({"id": pid, "layer": p["layer"], "area": p["area"], "n": len(p["points"]),
                      "bbox": [min(xs), min(ys), max(xs), max(ys)]})
    return json.dumps({"profiles": profs, "ops": doc["ops"], "notes": doc.get("notes", ""),
                       "dxf_texts": doc.get("texts", [])}, ensure_ascii=False)


def _run_tool(doc, name, inp):
    if name == "upsert_op":
        op = inp["op"]
        for i, o in enumerate(doc["ops"]):
            if o["id"] == op["id"]:
                doc["ops"][i] = op; break
        else:
            idx = next((i + 1 for i, o in enumerate(doc["ops"]) if o["id"] == inp.get("after")), len(doc["ops"]))
            doc["ops"].insert(idx, op)
    elif name == "delete_op":
        doc["ops"] = [o for o in doc["ops"] if o["id"] != inp["id"]]
    _, errors = model.build(doc)
    return json.dumps({"ok": not errors, "build_errors": errors}, ensure_ascii=False)


def chat(doc, history, user_msg, max_rounds=8):
    """Gibt (neues_doc, antworttext, history) zurück. doc wird nur bei Erfolg übernommen."""
    client = anthropic.Anthropic()
    work = copy.deepcopy(doc)
    msgs = history + [{"role": "user",
                       "content": f"<modellzustand>{_context(work)}</modellzustand>\n{user_msg}"}]
    text = ""
    for _ in range(max_rounds):
        r = client.messages.create(model=MODEL, max_tokens=4096, system=SYSTEM, tools=TOOLS, messages=msgs)
        msgs.append({"role": "assistant", "content": r.content})
        text = "".join(b.text for b in r.content if b.type == "text") or text
        uses = [b for b in r.content if b.type == "tool_use"]
        if not uses:
            break
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u.id, "content": _run_tool(work, u.name, u.input)}
            for u in uses]})
    # Verlauf schlank halten: nur Text-Turns speichern
    hist = history + [{"role": "user", "content": user_msg}, {"role": "assistant", "content": text or "ok"}]
    return work, text, hist[-20:]
