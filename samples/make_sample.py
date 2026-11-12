"""Erzeugt Beispiel-DXF: Raum 6x4 m mit 0,3 m Wand (Außen/Innen-Kontur) + Stütze (Kreis)."""
import ezdxf
d = ezdxf.new(setup=True); d.header["$INSUNITS"] = 4; m = d.modelspace()
m.add_lwpolyline([(0,0),(6000,0),(6000,4000),(0,4000)], close=True, dxfattribs={"layer": "WAND_AUSSEN"})
m.add_lwpolyline([(300,300),(5700,300),(5700,3700),(300,3700)], close=True, dxfattribs={"layer": "WAND_INNEN"})
m.add_circle((3000,2000), 200, dxfattribs={"layer": "STUETZE"})
m.add_mtext("Fenster 1200x1400, Brüstung 900", dxfattribs={"layer": "TEXT"})
d.saveas("samples/raum.dxf")
