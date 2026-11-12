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
- view_hull {front,top,side (Profil-ids von Ansichten), margin=0, side_flip=auto|true|false}  Hüllkörper aus den Silhouetten der Ansichten (Schnitt der Extrusionen). X=Breite der Vorderansicht, Y=Tiefe, Z=Höhe. Für Störkantenmodelle der Standard.
- round_trim {target,x,y,r,z0,z1,top=false}  Ecken um einen runden Körper abtragen (Quadrat minus Zylinder) im Höhenband z0..z1; x,y relativ zur Hüllkörper-Ursprung. Überstände über das Quadrat hinaus bleiben erhalten; top=true: Band reicht bis zur Oberkante.
- move {target,dx,dy,dz}
- offset {target,distance}  Aufmaß-Hüllkörper um einen Körper (Störkante), envelope=true
- bbox_envelope {target?,margin}  Hüllquader um Körper oder alles
- delete {target}
Bei PDF-Maschinenzeichnungen enthält 'profiles' erkannte Ansichten (v1..: konvexe Umrisse in mm, 'view.role' = Heuristik front/top/side, Isometrie hat role null).
'rund_kandidaten' sind automatisch erkannte runde Körper (Kreis im Grundriss x Höhenband der Vorderansicht). Übernimm sie als round_trim-Operationen auf den Hüllkörper, sofern sie zum Bild passen.
Prüfe anhand des Bildes und der Bemaßung, ob Maßstab und Rollen stimmen; korrigiere die Rollen in den ops (nicht im Profil). Weicht eine Gesamtmaß-Bemaßung
deutlich (>5 %) von der Ansichtsgröße ab, nenne das in der Antwort mit (?).
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
        e = {"id": pid, "layer": p["layer"], "area": p["area"], "n": len(p["points"]),
             "bbox": [min(xs), min(ys), max(xs), max(ys)]}
        if p.get("view"): e["view"] = p["view"]
        if p.get("holes"): e["holes"] = len(p["holes"])
        profs.append(e)
    return json.dumps({"profiles": profs, "ops": doc["ops"], "notes": doc.get("notes", ""),
                       "modus": doc.get("mode"), "rund_kandidaten": doc.get("rounds", []), "massstab": doc.get("scale"),
                       "zeichnungstext": (doc.get("texts") or "")[:3000] if isinstance(doc.get("texts"), str)
                       else doc.get("texts", [])}, ensure_ascii=False)


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


def chat(doc, history, user_msg, max_rounds=8, image_png=None):
    """Gibt (neues_doc, antworttext, history) zurück. doc wird nur bei Erfolg übernommen."""
    client = anthropic.Anthropic(timeout=300)
    work = copy.deepcopy(doc)
    text_block = {"type": "text", "text": f"<modellzustand>{_context(work)}</modellzustand>\n{user_msg}"}
    content = [text_block]
    if image_png:
        import base64
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.b64encode(image_png).decode()}}, text_block]
    msgs = history + [{"role": "user", "content": content}]
    text = ""
    for _ in range(max_rounds):
        r = client.messages.create(model=MODEL, max_tokens=8192, system=SYSTEM, tools=TOOLS, messages=msgs)
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


def generate(doc, image_png=None):
    """Erstes Modell aus Zeichnung + Eingaben erzeugen."""
    mode = {"stoerkante": "Störkantenmodell (Hüllkörper/Kollisionsbereich um die Maschine; envelope=true)",
            "gebaeude": "Gebäudemodell (Wände, Öffnungen, Stützen)"}.get(doc.get("mode"), doc.get("mode"))
    msg = (f"Erzeuge jetzt das erste Modell aus der Zeichnung. Modus: {mode}. Maßstab 1:{doc.get('scale')}. "
           f"Aufmaß/Hinweise des Nutzers: {doc.get('notes') or '(keine)'}. "
           "Lege die Operationen mit upsert_op an, prüfe Baufehler und antworte mit einer kurzen Zusammenfassung "
           "(Maße, Annahmen mit (?)).")
    return chat(doc, [], msg, max_rounds=20, image_png=image_png)
