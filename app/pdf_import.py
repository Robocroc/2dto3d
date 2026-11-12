"""PDF (CAD-Vektor-PDF) -> Ansichten (konvexe Umrisse) in mm + Seitenbild + Texte."""
import math, re
import pymupdf
from shapely.geometry import MultiPoint, LineString, Polygon
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


NICE = [0.1, 0.2, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 500, 1000]


def _snap(x):
    n = min(NICE, key=lambda c: abs(math.log(x / c)))
    return n if abs(math.log(x / n)) < 0.04 else round(x, 2)


def detect_scale_from_dims(page):
    """Maßstab aus Bemaßungen: Zahl im Maßtext / Länge der zugehörigen Maßlinie (auf Papier).
    Rückgabe: dict(value, votes, total) oder None. Mehrheitsentscheid über alle Maße."""
    rot = page.rotation_matrix
    H, V = [], []                      # Linien (Koordinate, von, bis), nur dünne, achsenparallele
    for d in page.get_drawings():
        if (d.get("width") or 0) > 0.5: continue
        for it in d["items"]:
            if it[0] != "l": continue
            a, b = it[1] * rot, it[2] * rot
            if abs(a.y - b.y) < 0.3 and abs(a.x - b.x) > 4: H.append((a.y, min(a.x, b.x), max(a.x, b.x)))
            elif abs(a.x - b.x) < 0.3 and abs(a.y - b.y) > 4: V.append((a.x, min(a.y, b.y), max(a.y, b.y)))

    def span(lines, c, ctr, lo, hi, dist, half):
        """Maßlinie nahe Textmitte: durchgehend (deckt ctr ab) oder mit Lücke am Text (links/rechts verbinden)."""
        near = sorted({round(l[0], 1) for l in lines if abs(l[0] - c) <= dist})
        for pos in sorted(near, key=lambda q: abs(q - c)):
            seg = [l for l in lines if abs(l[0] - pos) < 0.4]
            cover = [l for l in seg if l[1] - 1 <= ctr <= l[2] + 1]
            if cover:
                a, b = min(l[1] for l in cover), max(l[2] for l in cover)
                return b - a
            left = [l for l in seg if ctr - half - 14 <= l[2] <= ctr + 1 and l[1] < lo]
            right = [l for l in seg if ctr - 1 <= l[1] <= ctr + half + 14 and l[2] > hi]
            if left and right:
                return max(l[2] for l in right) - min(l[1] for l in left)
        return None

    votes = []
    for *bb, w, _b, _l, _w in page.get_text("words"):
        if not re.fullmatch(r"\d{2,5}(?:[.,]\d)?", w): continue
        r = (pymupdf.Rect(*bb) * rot).normalize()
        x0, y0, x1, y1 = r.x0, r.y0, r.x1, r.y1
        v = float(w.replace(",", "."))
        wd, ht = x1 - x0, y1 - y0
        if wd >= ht:                    # horizontaler Text -> horizontale Maßlinie
            L = span(H, (y0 + y1) / 2, (x0 + x1) / 2, x0, x1, 1.8 * ht, wd / 2)
        else:                           # vertikaler Text -> vertikale Maßlinie
            L = span(V, (x0 + x1) / 2, (y0 + y1) / 2, y0, y1, 1.8 * wd, ht / 2)
        if L and L * PT >= 5:
            votes.append((v, L * PT))      # (Maßwert real, Maßlinie auf Papier in mm)
    if len(votes) < 3: return None
    # 1. Grobschätzung per Mehrheit (breite Klassen), 2. Gerade L = v/s - a (a = Pfeillänge) an Inlier anpassen
    bins = {}
    for v, l in votes: bins.setdefault(round(math.log(v / l) / 0.1), []).append((v, l))
    cl = max(bins.values(), key=len)
    if len(cl) < 3: return None
    s0 = sorted(v / l for v, l in cl)[len(cl) // 2]
    inl = [(v, l) for v, l in votes if abs(math.log(v / l / s0)) < 0.25]
    for _ in range(3):                      # robuste Regression (Ausreißer iterativ entfernen)
        n = len(inl)
        if n < 3 or max(v for v, _ in inl) < 1.5 * min(v for v, _ in inl): break
        mv, ml = sum(v for v, _ in inl) / n, sum(l for _, l in inl) / n
        slope = sum((v - mv) * (l - ml) for v, l in inl) / sum((v - mv) ** 2 for v, _ in inl)
        icpt = ml - slope * mv
        if slope <= 0: break
        keep = [(v, l) for v, l in inl if abs(l - (slope * v + icpt)) < 1.5]
        if len(keep) == len(inl) or len(keep) < 3: inl = keep if len(keep) >= 3 else inl; break
        inl = keep
    n = len(inl)
    mv, ml = sum(v for v, _ in inl) / n, sum(l for _, l in inl) / n
    spread = sum((v - mv) ** 2 for v, _ in inl)
    slope = sum((v - mv) * (l - ml) for v, l in inl) / spread if spread else 0
    icpt = ml - slope * mv
    if slope > 0 and -12 <= icpt <= 0.5 and abs(math.log((1 / slope) / s0)) < 0.15:   # Pfeilversatz plausibel
        value = _snap(1 / slope)
    else:
        value = _snap(s0)
    return {"value": value, "votes": len(inl), "total": len(votes)}


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


def _silhouette(group, H, k, gap_pt):
    """Echte (nicht konvexe) Umrisse einer Ansicht: Linien puffern (schließt kleine Lücken), Innenräume füllen."""
    lines = [LineString([(x * k, (H - y) * k) for x, y in seg]) for seg in group if len(seg) >= 2]
    r = 0.8 * k                                   # ~0,8 pt Papier -> mm real
    import numpy as np, shapely
    arr = shapely.buffer(np.array(lines, dtype=object), r, quad_segs=1, join_style=2)
    u = shapely.union_all(arr)
    polys = [Polygon(q.exterior) for q in (u.geoms if hasattr(u, "geoms") else [u])]   # Löcher füllen
    polys = [q for q in unary_union(polys).geoms] if len(polys) > 1 and unary_union(polys).geom_type == "MultiPolygon" \
        else [unary_union(polys)]
    polys = sorted([q for q in polys if q.geom_type == "Polygon"], key=lambda q: -q.area)
    if not polys: return []
    big = polys[0]
    bx = big.bounds
    size = max(bx[2] - bx[0], bx[3] - bx[1])
    close, tol = 0.012 * size, 0.004 * size          # Kerben schließen, Kontur glätten (Hüllkontur statt Detail)
    out = []
    for q in polys:
        if q.area < 0.01 * big.area: continue
        q = Polygon(q.buffer(close, join_style=2).buffer(-close, join_style=2).exterior).simplify(tol)
        if q.geom_type == "Polygon" and not q.is_empty: out.append(q)
    return out[:6]


def _fit_circle(pts):
    import numpy as np
    a = np.array(pts); x, y = a[:, 0], a[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones(len(x))]
    sol, *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    cx, cy = sol[0], sol[1]
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 0))
    res = np.hypot(x - cx, y - cy) - r
    ang = np.sort(np.arctan2(y - cy, x - cx))
    gap = max(np.max(np.diff(ang)), 2 * math.pi - (ang[-1] - ang[0])) if len(ang) > 2 else 6.3
    return cx, cy, r, float(np.sqrt(np.mean(res ** 2))), math.degrees(gap)


def find_circles(segs, size, tol):
    """Kreise aus Linienketten (Polylinien/Bezier-Abtastung) einer Ansicht. segs: Punktlisten in mm."""
    nodes, edges = {}, []
    key = lambda p: (round(p[0] / tol), round(p[1] / tol))
    for pts in segs:
        for a, b in zip(pts, pts[1:]):
            if math.dist(a, b) < 1e-6: continue
            e = len(edges); edges.append((key(a), key(b), a, b))
            nodes.setdefault(key(a), []).append(e); nodes.setdefault(key(b), []).append(e)
    seen, circles = set(), []
    for e0, (ka, kb, a, b) in enumerate(edges):
        if e0 in seen: continue
        chain, ends = [a, b], [ka, kb]
        seen.add(e0)
        for side in (1, 0):                        # Kette in beide Richtungen verlängern
            cur = ends[side]
            while True:
                nxt = [e for e in nodes[cur] if e not in seen]
                if len(nodes[cur]) != 2 or not nxt: break
                e = nxt[0]; seen.add(e)
                ka2, kb2, a2, b2 = edges[e]
                nk, np_ = (kb2, b2) if ka2 == cur else (ka2, a2)
                if side: chain.append(np_)
                else: chain.insert(0, np_)
                cur = nk
        if len(chain) < 8: continue
        cx, cy, r, rms, gap = _fit_circle(chain)
        if 0.02 * size <= r <= 0.6 * size and rms < 0.005 * r and gap < 150:
            circles.append((cx, cy, r))
    out = []
    for c in sorted(circles, key=lambda c: -c[2]):         # Duplikate (gleicher Kreis aus mehreren Ketten) entfernen
        if not any(math.dist(c[:2], o[:2]) < 2 and abs(c[2] - o[2]) < 2 for o in out): out.append(c)
    return out


def round_features(top_segs, front_segs, top_pts, front_pts, size_top, tol):
    """Runde Körper: Kreise im Grundriss x waagerechte Kanten gleicher Breite in der Vorderansicht -> Höhenbänder."""
    tx = lambda pts: (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    top_cx, top_y0 = tx(top_pts), min(p[1] for p in top_pts)
    front_cx, front_z0 = tx(front_pts), min(p[1] for p in front_pts)
    hor = {}
    for pts in front_segs:
        for a, b in zip(pts, pts[1:]):
            if abs(a[1] - b[1]) < tol and abs(a[0] - b[0]) > 1:
                hor.setdefault(round(a[1] / (2 * tol)), []).append((min(a[0], b[0]), max(a[0], b[0])))
    lv = []
    for k_, iv in hor.items():                      # kollineare Stücke zusammenfassen
        iv.sort(); cur = list(iv[0])
        for lo, hi in iv[1:]:
            if lo <= cur[1] + 2 * tol: cur[1] = max(cur[1], hi)
            else: lv.append((k_ * 2 * tol, cur[0], cur[1])); cur = [lo, hi]
        lv.append((k_ * 2 * tol, cur[0], cur[1]))
    feats = []
    groups = []                                   # konzentrische Kreise zu einer Gruppe (Flansch = Ringstapel)
    for c in find_circles(top_segs, size_top, tol):
        for g_ in groups:
            if math.hypot(c[0] - g_[0][0], c[1] - g_[0][1]) < 3: g_.append(c); break
        else: groups.append([c])
    for g_ in groups:
        cx, cy = g_[0][0], g_[0][1]
        rlo, rhi = min(c[2] for c in g_), max(c[2] for c in g_)
        xw = cx - top_cx
        tl = 0.02 * 2 * rhi + 1.5
        hits = sorted({(round(y - front_z0, 1), round((hi - lo) / 2, 1)) for y, lo, hi in lv
                       if 2 * rlo - tl <= hi - lo <= 2 * rhi + tl and abs((lo + hi) / 2 - front_cx - xw) <= 0.03 * 2 * rhi + 2})
        bands = []
        for z, rr in hits:
            if bands and z - bands[-1][-1][0] <= 30: bands[-1].append((z, rr))
            else: bands.append([(z, rr)])
        feats += [(xw, cy - top_y0, max(r_ for _, r_ in b), b[0][0], b[-1][0]) for b in bands
                  if len(b) >= 2 and b[-1][0] - b[0][0] >= 10]
    out = []
    for f in sorted(feats, key=lambda f: -f[2]):            # Überlappende Bänder gleicher Achse: größerer Radius gewinnt
        if not any(math.hypot(f[0] - o[0], f[1] - o[1]) < 3 and abs(f[2] - o[2]) < 0.04 * o[2] and f[3] < o[4] and f[4] > o[3] for o in out):
            out.append(f)
    zt = max(p[1] for p in front_pts) - front_z0
    res = []
    for f in out:
        to_top = zt - f[4] <= 0.04 * zt                          # Band reicht bis zur Oberkante
        d = {"x": round(float(f[0]), 1), "y": round(float(f[1]), 1), "r": round(float(f[2]), 1),
             "z0": round(float(f[3]), 1), "z1": round(float(zt if to_top else f[4]), 1)}
        if to_top: d["top"] = True
        res.append(d)
    return res


def extract(path, scale=None, page_no=0, gap_pt=10, min_width=0.8, rooms=False):
    doc = pymupdf.open(path)
    page = doc[page_no]
    H, W = page.rect.height, page.rect.width
    words = page.get_text("words")
    dims = None if scale else detect_scale_from_dims(page)
    detected = (dims or {}).get("value") or detect_scale(words)
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
        hull = MultiPoint(pts).convex_hull
        if hull.geom_type != "Polygon": continue
        parts = _silhouette(g, H, k, gap_pt)
        main = parts[0] if parts else hull
        views.append({"bbox": [round(v, 1) for v in bbox], "area": main.area, "role": None, "hull": hull,
                      "parts": parts or [hull], "segs": [[(x * k, (H - y) * k) for x, y in sg] for sg in g]})
    views.sort(key=lambda v: -v["area"])
    views = [v for v in views if v["area"] >= 0.02 * views[0]["area"]] if views else views   # Schriftfeld-/Detailrauschen
    _roles(views, H * k)
    profiles = {}
    for i, v in enumerate(views, 1):
        b = v["bbox"]
        rp = lambda poly: [[round(x, 1), round(y, 1)] for x, y in list(poly.exterior.coords)[:-1]]
        profiles[f"v{i}"] = {"layer": "ansicht", "points": rp(v["parts"][0]), "parts": [rp(q) for q in v["parts"][1:]],
                             "hull": rp(v["hull"].simplify(1.0)), "area": round(v["area"], 1),
                             "view": {"role": v["role"], "size": [round(b[2] - b[0], 1), round(b[3] - b[1], 1)]}}
    rounds = []
    byrole = {v["role"]: v for v in views if v["role"]}
    if "top" in byrole and "front" in byrole:
        t, f = byrole["top"], byrole["front"]
        try:
            rounds = round_features(t["segs"], f["segs"], [tuple(p) for p in t["parts"][0].exterior.coords],
                                    [tuple(p) for p in f["parts"][0].exterior.coords],
                                    max(t["bbox"][2] - t["bbox"][0], t["bbox"][3] - t["bbox"][1]), 0.1 * k)
        except Exception as e:
            print("Rund-Erkennung fehlgeschlagen:", e)
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
    info = {"scale": scale, "scale_detected": detected, "scale_votes": dims, "page_mm": [round(W * PT * scale), round(H * PT * scale)],
            "rounds": rounds, "n_contour": len(segs), "n_thin": len(thin)}
    return profiles, text, info
