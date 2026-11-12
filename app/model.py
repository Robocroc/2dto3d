"""CAD-Kern: Operationsliste (JSON) -> OpenCascade-Körper (CadQuery)."""
import cadquery as cq

OP_TYPES = ["extrude", "box", "cylinder", "cut_box", "cut_cylinder", "cut_extrude",
            "move", "offset", "bbox_envelope", "delete"]


def _pts(op, doc):
    if op.get("profile"):
        p = doc["profiles"][op["profile"]]["points"]
    else:
        p = op["points"]
    p = [tuple(map(float, q)) for q in p]
    if len(p) > 1 and p[0] == p[-1]:
        p = p[:-1]
    return p


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
        s = cq.Workplane("XY").workplane(offset=op.get("z", 0)) \
            .polyline(_pts(op, doc)).close().extrude(op["height"]).val()
        bodies[oid] = {"shape": s, **meta}
    elif t == "box":
        bodies[oid] = {"shape": _box(op), **meta}
    elif t == "cylinder":
        bodies[oid] = {"shape": _cyl(op), **meta}
    elif t == "cut_extrude":   # Profil (z.B. Innenkontur) aus Ziel ausschneiden
        tool = cq.Workplane("XY").workplane(offset=op.get("z", 0)) \
            .polyline(_pts(op, doc)).close().extrude(op["height"]).val()
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
