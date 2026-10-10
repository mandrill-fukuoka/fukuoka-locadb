"""テスト用の架空の地図（.osm XML）を作る

2つの区（四角形）：中央区 lon 130.38–130.42、博多区 lon 130.42–130.46（lat 33.58–33.60）
"""
from pathlib import Path
from xml.sax.saxutils import quoteattr

from locadb import osm_source
from locadb.pipeline import run_sync


class World:
    def __init__(self):
        self.nodes: dict[int, tuple] = {}          # id -> (lon, lat, tags)
        self.ways: dict[int, tuple] = {}           # id -> (node_ids, tags)
        self.rels: dict[int, tuple] = {}           # id -> (members[(type, ref, role)], tags)
        self._nid = 1_000_000
        self._wid = 2_000_000
        self._ward(1, 130.38, 130.42)
        self._ward(2, 130.42, 130.46)

    def _new_node(self, lon, lat, tags=None):
        self._nid += 1
        self.nodes[self._nid] = (lon, lat, tags or {})
        return self._nid

    def _ward(self, rid, lon0, lon1):
        pts = [(lon0, 33.58), (lon1, 33.58), (lon1, 33.60), (lon0, 33.60)]
        ids = [self._new_node(*p) for p in pts]
        self._wid += 1
        self.ways[self._wid] = (ids + [ids[0]], {})
        self.rels[rid] = ([("way", self._wid, "outer")],
                          {"type": "boundary", "boundary": "administrative", "admin_level": "8", "name": f"区{rid}"})

    # ---- 地物 ----
    def point(self, nid, lon, lat, **tags):
        self.nodes[nid] = (lon, lat, tags)

    def line(self, wid, coords, **tags):
        ids = [self._new_node(lon, lat) for lon, lat in coords]
        self.ways[wid] = (ids, tags)

    def area(self, wid, lon, lat, size=0.001, **tags):
        pts = [(lon - size, lat - size), (lon + size, lat - size), (lon + size, lat + size), (lon - size, lat + size)]
        ids = [self._new_node(*p) for p in pts]
        self.ways[wid] = (ids + [ids[0]], tags)

    def remove(self, kind, oid):
        getattr(self, kind).pop(oid, None)

    # ---- 書き出し・実行 ----
    def write(self, path: Path) -> Path:
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="test">']
        tag = lambda t: "".join(f'<tag k={quoteattr(k)} v={quoteattr(str(v))}/>' for k, v in t.items())
        for nid, (lon, lat, t) in sorted(self.nodes.items()):
            out.append(f'<node id="{nid}" version="1" lat="{lat:.7f}" lon="{lon:.7f}">{tag(t)}</node>')
        for wid, (ids, t) in sorted(self.ways.items()):
            out.append(f'<way id="{wid}" version="1">' + "".join(f'<nd ref="{i}"/>' for i in ids) + tag(t) + "</way>")
        for rid, (mem, t) in sorted(self.rels.items()):
            out.append(f'<relation id="{rid}" version="1">'
                       + "".join(f'<member type="{ty}" ref="{r}" role="{ro}"/>' for ty, r, ro in mem) + tag(t) + "</relation>")
        out.append("</osm>")
        path.write_text("\n".join(out), encoding="utf-8")
        return path

    def run(self, conn, tmp_path, mode="apply", allow=False):
        p = self.write(tmp_path / f"w{len(list(tmp_path.glob('*.osm')))}.osm")
        return run_sync(conn, lambda watch: (osm_source.read(p, watch), {"file": str(p)}), mode, allow)


def spot(conn, **where):
    k, v = next(iter(where.items()))
    return conn.execute(f"select *, ST_X(geom::geometry) lon, ST_Y(geom::geometry) lat from spots where {k}=%s", (v,)).fetchone()


def spots(conn, sql="true", *args):
    return conn.execute(f"select *, ST_X(geom::geometry) lon, ST_Y(geom::geometry) lat from spots where {sql} order by id",
                        args).fetchall()


def events(conn, spot_id):
    return [r["event"] for r in conn.execute("select event from spot_history where spot_id=%s order by id", (spot_id,))]
