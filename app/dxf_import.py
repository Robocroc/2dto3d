"""DXF -> geschlossene 2D-Profile (mm)."""
import math
import ezdxf
from ezdxf import path as ezpath

UNITS = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}


def _area(p):
    return 0.5 * sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
                     for i in range(len(p)))


def _chain(segs, tol=0.5):
    """Lose LINE/ARC-Segmente (Punktlisten) zu geschlossenen Ringen verketten."""
    rings, segs = [], [list(s) for s in segs]
    d = lambda a, b: math.dist(a, b)
    while segs:
        cur = segs.pop()
        grown = True
        while grown and d(cur[0], cur[-1]) > tol:
            grown = False
            for i, s in enumerate(segs):
                if d(cur[-1], s[0]) <= tol: cur += s[1:]
                elif d(cur[-1], s[-1]) <= tol: cur += s[::-1][1:]
                elif d(cur[0], s[-1]) <= tol: cur = s[:-1] + cur
                elif d(cur[0], s[0]) <= tol: cur = s[::-1][:-1] + cur
                else: continue
                segs.pop(i); grown = True; break
        if d(cur[0], cur[-1]) <= tol and len(cur) >= 4:
            rings.append(cur[:-1])
    return rings


def import_dxf(file_path):
    doc = ezdxf.readfile(file_path)
    k = UNITS.get(doc.header.get("$INSUNITS", 4), 1.0)
    ms, closed, loose = doc.modelspace(), [], []
    for e in ms:
        t, layer = e.dxftype(), e.dxf.layer
        try:
            if t in ("LWPOLYLINE", "POLYLINE") and e.is_closed:
                closed.append((layer, [(v.x, v.y) for v in ezpath.make_path(e).flattening(0.5)]))
            elif t == "CIRCLE":
                closed.append((layer, [(v.x, v.y) for v in ezpath.make_path(e).flattening(0.5)]))
            elif t in ("LINE", "ARC", "LWPOLYLINE", "POLYLINE"):
                loose.append((layer, [(v.x, v.y) for v in ezpath.make_path(e).flattening(0.5)]))
        except Exception:
            pass
    for layer in {l for l, _ in loose}:
        for ring in _chain([p for l, p in loose if l == layer]):
            closed.append((layer, ring))
    profiles = {}
    for n, (layer, pts) in enumerate(closed, 1):
        pts = [(x * k, y * k) for x, y in pts]
        if len(pts) < 3 or abs(_area(pts)) < 1e-6:
            continue
        profiles[f"p{n}"] = {"layer": layer, "points": [[round(x, 3), round(y, 3)] for x, y in pts],
                             "area": round(abs(_area(pts)), 1)}
    texts = [e.dxf.text for e in ms if e.dxftype() in ("TEXT",)] + \
            [e.text for e in ms if e.dxftype() == "MTEXT"]
    return profiles, texts
