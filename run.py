"""Ein-Klick-Start: richtet .venv ein, installiert Abhängigkeiten, startet die GUI und öffnet den Browser.
Aufruf:  python run.py        (Python 3.10-3.12 empfohlen)"""
import io, os, subprocess, sys, threading, time, urllib.request, venv, webbrowser, zipfile
from pathlib import Path

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PORT = 8765
REPO, BRANCH = "Robocroc/2dto3d", "main"
KEEP = (".venv", "project.json", "uploads", ".git/")       # nie überschreiben


def update():
    """Aktuellen Stand als ZIP von GitHub laden und über die lokalen Dateien kopieren.
    Privates Repo: Umgebungsvariable GITHUB_TOKEN setzen (Token mit Leserecht auf das Repo)."""
    url = os.environ.get("STK_UPDATE_URL") or f"https://api.github.com/repos/{REPO}/zipball/{BRANCH}"
    hdr = {"User-Agent": "stk-updater"}
    if os.environ.get("GITHUB_TOKEN"):
        hdr["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        data = urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60).read()
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        print(f"Update übersprungen ({e}). Privates Repo? -> GITHUB_TOKEN setzen oder Repo öffentlich machen.")
        return
    top, n = zf.namelist()[0].split("/")[0], 0
    (ROOT / ".build_sha").write_text(top.rsplit("-", 1)[-1][:7])        # Zip-Ordner heißt <owner>-<repo>-<sha>
    for name in zf.namelist():
        rel = name[len(top) + 1:]
        if not rel or name.endswith("/") or rel.startswith(KEEP):
            continue
        target = ROOT / rel
        new = zf.read(name)
        if target.exists() and target.read_bytes() == new:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(new); n += 1
    print(f"Update: {n} Datei(en) aktualisiert." if n else "Update: bereits aktuell.")


if "--no-update" not in sys.argv:
    update()
if "--update" in sys.argv:           # nur aktualisieren, nicht starten
    sys.exit(0)

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
