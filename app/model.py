"""CAD-Kern: Operationsliste (JSON) -> OpenCascade-Körper (CadQuery)."""
import cadquery as cq

OP_TYPES = ["extrude", "box", "cylinder", "cut_box", "cut_cylinder", "cut_extrude",
            "move", "offset", "bbox_envelope", "view_hull", "delete"]


def _pts(op, doc):
    if op.get("profile"):
        p = doc["profiles"][op["profile"]]["points"]
    else:
        p = op["points"]
    p = [tuple(map(float, q)) for q in p]
    if len(p) > 1 and p[0] == p[-1]:
        p = p[:-1]
    return p


def _prism(op, doc):
    """Profil extrudieren (inkl. optionaler Löcher aus Profil)."""
    z, h = op.get("z", 0), op["height"]
    s = cq.Workplane("XY").workplane(offset=z).polyline(_pts(op, doc)).close().extrude(h).val()
    for hole in (doc["profiles"].get(op.get("profile"), {}).get("holes") or []):
        s = s.cut(cq.Workplane("XY").workplane(offset=z).polyline([tuple(p) for p in hole]).close().extrude(h).val())
    return s


def _view_hull_shape(op, doc, flip):
    """Hüllkörper = Schnitt der extrudierten Ansichts-Silhouetten (Vorder-/Drauf-/Seitenansicht)."""
    from shapely.geometry import Polygon
    mg = float(op.get("margin", 0) or 0)

    def poly(pts):   # Aufmaß direkt an der Silhouette (robust, statt 3D-Offset)
        pts = [tuple(map(float, p)) for p in pts]
        return list(Polygon(pts).buffer(mg, join_style=2).exterior.coords)[:-1] if mg else pts
    P = {k: poly(doc["profiles"][op[k]]["points"]) for k in ("front", "top", "side") if op.get(k)}
    if len(P) < 2:
        raise ValueError("view_hull braucht mindestens zwei Ansichten (front/top/side)")
    mn = lambda pts, i: min(p[i] for p in pts)
    mx = lambda pts, i: max(p[i] for p in pts)
    ctr = lambda pts, i: (mn(pts, i) + mx(pts, i)) / 2
    L = 1e5
    parts = []
    if "front" in P:   # (u=X, v=Z), Extrusion entlang Y
        f = [(u - ctr(P["front"], 0), v - mn(P["front"], 1)) for u, v in P["front"]]
        parts.append(cq.Workplane("XY").workplane(offset=-L).polyline(f).close().extrude(2 * L)
                     .rotate((0, 0, 0), (1, 0, 0), 90).val())
    if "top" in P:     # (u=X, v=Y), Extrusion entlang Z
        t = [(u - ctr(P["top"], 0), v - mn(P["top"], 1)) for u, v in P["top"]]
        parts.append(cq.Workplane("XY").workplane(offset=-L).polyline(t).close().extrude(2 * L).val())
    if "side" in P:    # (u=Y, v=Z), Extrusion entlang X
        s = P["side"]
        pts = [((mx(s, 0) - u) if flip else (u - mn(s, 0)), v - mn(s, 1)) for u, v in s]
        parts.append(cq.Workplane("XY").workplane(offset=-L).polyline([(z, y) for y, z in pts]).close().extrude(2 * L)
                     .rotate((0, 0, 0), (0, 1, 0), -90).val())
    r = parts[0]
    for p in parts[1:]:
        r = r.intersect(p)
    return r


def _view_hull(op, doc):
    flip = op.get("side_flip", "auto")
    if flip == "auto" and op.get("side"):
        a, b = _view_hull_shape(op, doc, False), _view_hull_shape(op, doc, True)
        r = a if a.Volume() >= b.Volume() else b     # konsistentere Silhouetten = größeres Schnittvolumen
    else:
        r = _view_hull_shape(op, doc, bool(flip) and flip != "auto")
    return r


def _offset_shape(shape, d):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Intersection
    mk = BRepOffsetAPI_MakeOffsetShape()
    mk.PerformByJoin(shape.wrapped, float(d), 1e-3, BRepOffset_Skin,
                     False, False, GeomAbs_Intersection)
    return cq.Shape.cast(mk.Shape())


def _bbox_solid(shape, margin=0.0):
    b = shape.BoundingBox()
    return cq.Workplane("XY").box(
        b.xlen + 2 * margin, b.ylen + 2 * margin, b.zlen + 2 * margin,
        centered=False).translate((b.xmin - margin, b.ymin - margin, b.zmin - margin)).val()


def _box(op):
    return cq.Workplane("XY").box(op["dx"], op["dy"], op["dz"], centered=False) \
        .translate((op.get("x", 0), op.get("y", 0), op.get("z", 0))).val()


def _cyl(op):
    return cq.Workplane("XY").circle(op["r"]).extrude(op["h"]) \
        .translate((op.get("x", 0), op.get("y", 0), op.get("z", 0))).val()


def apply(op, doc, bodies):
    t, oid = op["type"], op["id"]
    meta = {"name": op.get("name", oid), "color": op.get("color"),
            "envelope": bool(op.get("envelope"))}
    if t == "extrude":
        bodies[oid] = {"shape": _prism(op, doc), **meta}
    elif t == "view_hull":
        bodies[oid] = {"shape": _view_hull(op, doc), **{**meta, "envelope": op.get("envelope", True)}}
    elif t == "box":
        bodies[oid] = {"shape": _box(op), **meta}
    elif t == "cylinder":
        bodies[oid] = {"shape": _cyl(op), **meta}
    elif t == "cut_extrude":   # Profil (z.B. Innenkontur) aus Ziel ausschneiden
        tool = _prism(op, doc)
        b = bodies[op["target"]]
        b["shape"] = b["shape"].cut(tool)
    elif t in ("cut_box", "cut_cylinder"):
        tool = _box(op) if t == "cut_box" else _cyl(op)
        b = bodies[op["target"]]
        b["shape"] = b["shape"].cut(tool)
    elif t == "move":
        b = bodies[op["target"]]
        b["shape"] = b["shape"].translate(
            cq.Vector(op.get("dx", 0), op.get("dy", 0), op.get("dz", 0)))
    elif t == "offset":      # Aufmaß / Störkontur um einen Körper
        src = bodies[op["target"]]["shape"]
        try:
            s = _offset_shape(src, op["distance"])
        except Exception:
            s = _bbox_solid(src, op["distance"])
        bodies[oid] = {"shape": s, **{**meta, "envelope": op.get("envelope", True)}}
    elif t == "bbox_envelope":   # Hüllquader um Körper (oder alle)
        tg = op.get("target")
        shapes = [bodies[tg]["shape"]] if tg else [b["shape"] for b in bodies.values()]
        comp = cq.Compound.makeCompound(shapes)
        bodies[oid] = {"shape": _bbox_solid(comp, op.get("margin", 0)),
                       **{**meta, "envelope": op.get("envelope", True)}}
    elif t == "delete":
        bodies.pop(op["target"], None)
    else:
        raise ValueError(f"Unbekannter Typ {t}")


def build(doc):
    bodies, errors = {}, []
    for op in doc.get("ops", []):
        try:
            apply(op, doc, bodies)
        except Exception as e:  # einzelne fehlerhafte Op blockiert nicht den Rest
            errors.append({"op": op.get("id"), "error": f"{type(e).__name__}: {e}"})
    return bodies, errors


def meshes(bodies, tol=1.0):
    out = []
    for oid, b in bodies.items():
        v, tri = b["shape"].tessellate(tol, 0.3)
        out.append({"id": oid, "name": b["name"], "color": b["color"],
                    "envelope": b["envelope"],
                    "vertices": [c for p in v for c in (p.x, p.y, p.z)],
                    "indices": [i for t in tri for i in t]})
    return out


def export_step(bodies, path):
    comp = cq.Compound.makeCompound([b["shape"] for b in bodies.values()])
    cq.exporters.export(cq.Workplane("XY").add(comp), path, "STEP")
