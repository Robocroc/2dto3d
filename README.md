# 2dto3d (Störkanten3D)
creates 3d interfearing edge models from 2d input

2D-Zeichnung (DXF) + Prosa -> parametrisches 3D-Modell -> STEP.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install cadquery ezdxf fastapi "uvicorn[standard]" anthropic python-multipart
export ANTHROPIC_API_KEY=...      # für die KI-Anpassung
cd app && uvicorn main:app --port 8765   # http://localhost:8765
```
Struktur: `model.py` (Operationen -> OpenCascade), `dxf_import.py`, `ai.py` (Claude Tool-Use), `main.py` (API), `static/` (Three.js-GUI).
Beispiel: `python samples/make_sample.py` -> `samples/raum.dxf`.
