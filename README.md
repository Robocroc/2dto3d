# 2dto3d (Störkanten3D)
creates 3d interfearing edge models from 2d input

2D-Zeichnung (DXF) + Prosa -> parametrisches 3D-Modell -> STEP.

## Schnellstart lokal
```bash
python run.py            # richtet alles ein, startet GUI, öffnet Browser
python cli.py zeichnung.pdf -o modell.stp --margin 50   # ohne GUI
```

`run.py` aktualisiert sich bei jedem Start selbst von GitHub (ZIP-Download, `.venv` und `project.json` bleiben unberührt).
Nur aktualisieren: `python run.py --update` · ohne Update starten: `python run.py --no-update`.
Privates Repo: Umgebungsvariable `GITHUB_TOKEN` (Token mit Leserecht) setzen, oder das Repo öffentlich machen.

## Manuell
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...      # für die KI-Anpassung
cd app && uvicorn main:app --port 8765   # http://localhost:8765
```
Struktur: `model.py` (Operationen -> OpenCascade), `dxf_import.py`, `ai.py` (Claude Tool-Use), `main.py` (API), `static/` (Three.js-GUI).
Beispiel: `python samples/make_sample.py` -> `samples/raum.dxf`.

## Im Browser (GitHub Codespaces)
Repo auf GitHub -> **Code -> Codespaces -> Create codespace on main**. Nach der Installation startet der Server
automatisch und der Browser öffnet Port 8765. Für die KI: unter GitHub -> Settings -> Codespaces -> Secrets
ein Secret `ANTHROPIC_API_KEY` anlegen (vor dem Start des Codespace) und dem Repo freigeben.
Neustart des Servers im Codespace-Terminal: `cd app && uvicorn main:app --host 0.0.0.0 --port 8765`

## Ablauf (Assistent in der GUI)
1. **Eingaben:** PDF (CAD-Vektor-PDF) oder DXF hochladen, Modus (Störkante/Gebäude), Maßstab 1:N, Aufmaß, Anmerkungen
2. **Modell erzeugen** (mit KI, ohne Key regelbasiert) -> 3. **Vorschau** -> 4. **Anpassen** per Prosa -> 5. **STEP-Download**

PDF-Import: Konturlayer (Linienbreite >= 0,8 pt, ohne `RAHM*`-Layer) werden zu Ansichten geclustert, je Ansicht ein konvexer Umriss;
`view_hull` schneidet Vorder-/Drauf-/Seitenansicht zum Hüllkörper. Beispiel: `samples/beispiel_siebmaschine.pdf` (Maßstab 5).
