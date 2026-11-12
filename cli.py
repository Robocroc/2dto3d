"""Kommandozeile ohne GUI:  python cli.py zeichnung.pdf -o modell.stp [--scale 5] [--margin 50]
Maßstab wird bei PDF automatisch aus den Bemaßungen gelesen, wenn --scale fehlt."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "app"))
import model, pdf_import, dxf_import  # noqa: E402

ap = argparse.ArgumentParser(description="2D-Zeichnung -> Störkanten-/Gebäudemodell -> STEP")
ap.add_argument("datei", help="PDF oder DXF")
ap.add_argument("-o", "--out", default="modell.stp")
ap.add_argument("--scale", type=float, help="Maßstab 1:N (N), sonst automatisch (nur PDF)")
ap.add_argument("--margin", type=float, default=0, help="Aufmaß in mm (Störkante)")
ap.add_argument("--mode", choices=["stoerkante", "gebaeude"], default="stoerkante")
ap.add_argument("--height", type=float, default=2800, help="Extrusionshöhe in mm (Gebäude/DXF)")
ap.add_argument("--page", type=int, default=0)
a = ap.parse_args()

f = Path(a.datei)
doc = {"profiles": {}, "ops": [], "notes": "", "texts": [], "generated": True}
if f.suffix.lower() == ".pdf":
    import pymupdf
    scale = a.scale
    if not scale:
        d = pdf_import.detect_scale_from_dims(pymupdf.open(str(f))[a.page])
        if not d: sys.exit("Maßstab nicht erkannt – bitte --scale angeben.")
        scale = d["value"]; print(f"Maßstab 1:{scale} erkannt (aus {d['votes']} von {d['total']} Bemaßungen) (?)")
    doc["profiles"], _, info = pdf_import.extract(str(f), scale, a.page, rooms=(a.mode == "gebaeude"))
    doc["rounds"] = info.get("rounds", [])
else:
    doc["profiles"], _ = dxf_import.import_dxf(str(f))

roles = {v["view"]["role"]: k for k, v in doc["profiles"].items() if v.get("view", {}).get("role")}
if a.mode == "stoerkante" and len(roles) >= 2:
    print("Ansichten:", roles)
    doc["ops"] = [{"id": "huelle", "type": "view_hull", "margin": a.margin, "envelope": True,
                   **{r: roles[r] for r in ("front", "top", "side") if r in roles}}]
    for i, rf in enumerate(doc.get("rounds") or [], 1):
        print(f"Runder Körper {i}: Ø{2 * rf['r']:.0f} mm, Höhe {rf['z0']:.0f}-{rf['z1']:.0f} mm")
        doc["ops"].append({"id": f"rund{i}", "type": "round_trim", "target": "huelle", **rf})
else:
    doc["ops"] = [{"id": f"e_{k}", "type": "extrude", "profile": k, "height": a.height}
                  for k, p in doc["profiles"].items() if not p.get("view")]
bodies, errors = model.build(doc)
for e in errors: print("Fehler:", e)
if not bodies: sys.exit("Kein Modell erzeugt.")
for k, b in bodies.items():
    bb = b["shape"].BoundingBox(); print(f"{k}: {bb.xlen:.0f} x {bb.ylen:.0f} x {bb.zlen:.0f} mm, {b['shape'].Volume()/1e6:.1f} l")
model.export_step(bodies, a.out); print("Geschrieben:", a.out)
