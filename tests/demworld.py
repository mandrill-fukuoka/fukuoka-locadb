"""テスト用の架空の標高タイル（国土地理院の標高PNGと同じ形式）

DemTiles(height, sea=None, missing=None, broken=())
  height(lat, lon) -> 標高（numpy配列で渡される）
  sea(lat, lon)    -> True の画素は無効値（海など）
  missing(path, z, x, y) -> True のタイルは存在しない（404）
  broken           -> 取得に失敗するタイル（FetchError）
NullTiles：どこにもタイルがない（既定。テストで本物の国土地理院に問い合わせないため）
"""
import io

import numpy as np
from PIL import Image

from locadb import terrain


def encode(h: np.ndarray, na: np.ndarray) -> bytes:
    x = np.round(h * 100).astype(np.int64)
    x = np.where(x < 0, x + 2 ** 24, x)
    x = np.where(na, 2 ** 23, x)
    rgb = np.stack([(x >> 16) & 255, (x >> 8) & 255, x & 255], axis=-1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="PNG")
    return buf.getvalue()


class DemTiles(terrain.Tiles):
    def __init__(self, height, sea=None, missing=None, broken=()):
        super().__init__(session=object())
        self.height, self.sea, self.missing, self.broken = height, sea, missing, set(broken)
        self.calls = []

    def fetch(self, path, z, x, y):
        self.calls.append((path, z, x, y))
        if (path, z, x, y) in self.broken:
            raise terrain.FetchError("broken")
        if self.missing and self.missing(path, z, x, y):
            return None
        n = 2 ** z * 256
        lon = (x * 256 + np.arange(256) + 0.5) / n * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y * 256 + np.arange(256) + 0.5) / n))))
        LON, LAT = np.meshgrid(lon, lat)
        na = self.sea(LAT, LON) if self.sea else np.zeros_like(LAT, dtype=bool)
        return encode(self.height(LAT, LON), na)


class NullTiles(terrain.Tiles):
    def __init__(self):
        super().__init__(session=object())

    def fetch(self, path, z, x, y):
        return None
