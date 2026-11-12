"""Ein-Klick-Start: richtet .venv ein, installiert Abhängigkeiten, startet die GUI und öffnet den Browser.
Aufruf:  python run.py        (Python 3.10-3.12 empfohlen)"""
import os, subprocess, sys, threading, time, venv, webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PORT = 8765

if not PY.exists():
    print("Erstelle virtuelle Umgebung …"); venv.create(VENV, with_pip=True)
stamp = VENV / ".deps_ok"
if not stamp.exists() or stamp.stat().st_mtime < (ROOT / "requirements.txt").stat().st_mtime:
    print("Installiere Abhängigkeiten (einmalig, dauert einige Minuten) …")
    subprocess.check_call([str(PY), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    stamp.touch()
if not os.environ.get("ANTHROPIC_API_KEY"):
    print("Hinweis: ANTHROPIC_API_KEY nicht gesetzt -> ohne KI (regelbasiert). Für KI vorher setzen.")
threading.Thread(target=lambda: (time.sleep(2.5), webbrowser.open(f"http://localhost:{PORT}")), daemon=True).start()
print(f"Starte auf http://localhost:{PORT}  (Beenden: Strg+C)")
subprocess.call([str(PY), "-m", "uvicorn", "main:app", "--port", str(PORT)], cwd=ROOT / "app")
