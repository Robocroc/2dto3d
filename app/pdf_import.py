"""PDF (CAD-Vektor-PDF) -> Ansichten (konvexe Umrisse) in mm + Seitenbild + Texte."""
import math, re
import pymupdf
from shapely.geometry import MultiPoint, LineString
from shapely.ops import unary_union, polygonize

PT = 25.4 / 72


def _skip_layer(name):
    n = (name or "").lower()
    return n.startswith("rahm")          # Rahmen/Schriftfeld/Stempel


def render(path, page_no=0, long_side=2000):
    doc = pymupdf.open(path)
    page = doc[page_no]
    r = page.rect
    z = long_side / max(r.width, r.height)
    return page.get_pixmap(matrix=pymupdf.Matrix(z, z)).tobytes("png"), len(doc)


def detect_scale(words):
    """Maßstab aus Schriftfeld: Zahl 'a:b' nahe dem Wort 'Maßstab'. -> Faktor (1:5 -> 5.0) oder None."""
    ref = [w for w in words if w[4].lower().startswith("maßstab") or w[4].lower().startswith("scale")]
    cand = [w for w in words if re.fullmatch(r"\d+(?:[.,]\d+)?:\d+(?:[.,]\d+)?", w[4])]
    if not ref or not cand:
        return None
    r = ref[0]
    w = min(cand, key=lambda c: math.hypot((c[0] + c[2]) / 2 - (r[0] + r[2]) / 2, (c[1] + c[3]) / 2 - (r[1] + r[3]) / 2))
    a, b = (float(x.replace(",", ".")) for x in w[4].split(":"))
    return b / a if a and b else None


def _points(it):
    op = it[0]
    if op == "l": return [it[1], it[2]]
    if op == "re": r = it[1]; return [pymupdf.Point(r.x0, r.y0), pymupdf.Point(r.x1, r.y0), pymupdf.Point(r.x1, r.y1), pymupdf.Point(r.x0, r.y1)]
    if op == "qu": q = it[1]; return [q.ul, q.ur, q.lr, q.ll]
    if op == "c":      # Bézier abtasten (Kontrollpunkte liegen außerhalb der Kurve)
        p0, c1, c2, p1 = it[1], it[2], it[3], it[4]
        out = []
        for i in range(9):
            t = i / 8; a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
            out.append(pymupdf.Point(a * p0.x + b * c1.x + c * c2.x + d * p1.x, a * p0.y + b * c1.y + c * c2.y + d * p1.y))
        return out
    return []


def _clusters(segs, cell):
    """Segmente (Punktlisten, pt) räumlich zu Gruppen verbinden (Raster + Union-Find)."""
    parent = {}
    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    def union(a, b): parent[find(a)] = find(b)
    seg_cell = []
    for pts in segs:
        cells = set()
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] or pts):
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) / (cell / 2)))
            for i in range(n + 1):
                cells.add((int((x0 + (x1 - x0) * i / n) // cell), int((y0 + (y1 - y0) * i / n) // cell)))
        cells = list(cells)
        for c in cells[1:]: union(cells[0], c)
        seg_cell.append(cells[0])
    # 8er-Nachbarschaft zwischen belegten Zellen
    occupied = set(parent)
    for (cx, cy) in list(occupied):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (cx + dx, cy + dy) in occupied: union((cx, cy), (cx + dx, cy + dy))
    groups = {}
    for pts, c in zip(segs, seg_cell):
        groups.setdefault(find(c), []).append(pts)
    return list(groups.values())


def _roles(views, page_h_mm):
    """Heuristik: Vorderansicht oben links, Draufsicht darunter, Seitenansicht rechts daneben."""
    if not views: return
    big = [v for v in views if v["area"] >= 0.15 * max(x["area"] for x in views)]
    top_y = max(v["bbox"][3] for v in big)
    row = [v for v in big if top_y - v["bbox"][3] <= 0.1 * page_h_mm]
    front = min(row, key=lambda v: v["bbox"][0])
    front["role"] = "front"
    fx0, fx1 = front["bbox"][0], front["bbox"][2]
    def xov(v): return max(0, min(fx1, v["bbox"][2]) - max(fx0, v["bbox"][0])) / (fx1 - fx0)
    below = [v for v in big if v is not front and xov(v) > 0.5 and v["bbox"][3] < front["bbox"][1] + 1]
    if below: max(below, key=lambda v: v["area"])["role"] = "top"
    right = [v for v in row if v is not front and v["bbox"][0] > front["bbox"][0]]
    if right: max(right, key=lambda v: v["area"])["role"] = "side"


def extract(path, scale=None, page_no=0, gap_pt=10, min_width=0.8, rooms=False):
    doc = pymupdf.open(path)
    page = doc[page_no]
    H, W = page.rect.height, page.rect.width
    words = page.get_text("words")
    detected = detect_scale(words)
    scale = scale or detected or 1.0
    k = PT * scale
    rot = page.rotation_matrix          # Vektorkoordinaten liegen im ungedrehten Seitenraum
    segs, thin = [], []
    for d in page.get_drawings():
        if _skip_layer(d.get("layer")): continue
        wdt = d.get("width") or 0
        for it in d["items"]:
            pts = [((q := p * rot).x, q.y) for p in _points(it)]
            if len(pts) < 2: continue
            (segs if wdt >= min_width else thin).append(pts)
    views = []
    groups = _clusters(segs, gap_pt)
    for g in groups:
        pts = [(x * k, (H - y) * k) for s in g for x, y in s]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        if max(bbox[2] - bbox[0], bbox[3] - bbox[1]) < 12 * PT * scale or len(g) < 8:
            continue
        hull = MultiPoint(pts).convex_hull.simplify(1.0)
        if hull.geom_type != "Polygon": continue
        views.append({"bbox": [round(v, 1) for v in bbox], "area": hull.area, "role": None,
                      "points": [[round(x, 1), round(y, 1)] for x, y in list(hull.exterior.coords)[:-1]]})
    views.sort(key=lambda v: -v["area"])
    views = [v for v in views if v["area"] >= 0.02 * views[0]["area"]] if views else views   # Schriftfeld-/Detailrauschen
    _roles(views, H * k)
    profiles = {}
    for i, v in enumerate(views, 1):
        b = v["bbox"]
        profiles[f"v{i}"] = {"layer": "ansicht", "points": v["points"], "area": round(v["area"], 1),
                             "view": {"role": v["role"], "size": [round(b[2] - b[0], 1), round(b[3] - b[1], 1)]}}
    if rooms:   # Gebäudegrundriss: geschlossene Flächen aus Linien (?)
        lines = [LineString([(x * k, (H - y) * k) for x, y in s]) for s in segs]
        faces = [f for f in polygonize(unary_union(lines)) if 20000 <= f.area <= 0.5 * W * H * k * k]
        faces = sorted(faces, key=lambda f: -f.area)[:60]
        for i, f in enumerate(faces, 1):
            f = f.simplify(1.0)
            profiles[f"r{i}"] = {"layer": "flaeche", "area": round(f.area, 1),
                                 "points": [[round(x, 1), round(y, 1)] for x, y in list(f.exterior.coords)[:-1]],
                                 "holes": [[[round(x, 1), round(y, 1)] for x, y in list(h.coords)[:-1]] for h in f.interiors]}
    text = page.get_text("text")
    info = {"scale": scale, "scale_detected": detected, "page_mm": [round(W * PT * scale), round(H * PT * scale)],
            "n_contour": len(segs), "n_thin": len(thin)}
    return profiles, text, info
