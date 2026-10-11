"""S1 boundaries: HDX COD-AB v03 Bangladesh ADM2 (BBS P-codes) to district attributes and dashboard TopoJSON.

Implements S1 and ING-1 (raw zip cached in bronze with metadata), and supplies the map geometry for VIS-1.
Source: OCHA ROAP / BBS, CC BY-IGO, https://data.humdata.org/dataset/cod-ab-bgd

Counts are checked, not assumed: the build fails unless the file has exactly 64 districts in 8 divisions,
which is what COD-AB v03 publishes. The P-code (`adm2_pcode`, e.g. BD3026) is the join key everywhere.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile
import zipfile

import pandas as pd

from .common import BRONZE, DASHBOARD_DATA, download

URL = ("https://data.humdata.org/dataset/401d3fae-4262-48c9-891f-461fd776d49b/resource/"
       "cec2abe3-d8b7-4025-9362-9f7e780f2a07/download/bgd_admin_boundaries.geojson.zip")
SOURCE = "hdx-cod-ab"
ZIP_DEST = BRONZE / "boundaries" / f"source={SOURCE}" / "bgd_admin_boundaries.geojson.zip"
ADM2_MEMBER = "bgd_admin2.geojson"
TOPO_OUT = DASHBOARD_DATA / "bd_districts.topo.json"
TOPO_MAX_BYTES = 300_000
MAPSHAPER = "mapshaper@0.7.83"
EXPECTED_DISTRICTS, EXPECTED_DIVISIONS = 64, 8


def fetch():
    return download(URL, ZIP_DEST, SOURCE)


def read_adm2(zip_path=ZIP_DEST):
    """Parse the ADM2 GeoJSON straight out of the zip (read as bytes; nothing is extracted to disk)."""
    with zipfile.ZipFile(zip_path) as z:
        return json.loads(z.read(ADM2_MEMBER))


def districts_frame(geo):
    rows = []
    for f in geo["features"]:
        p = f["properties"]
        rows.append({
            "district_code": p["adm2_pcode"],
            "district_name": p["adm2_name"],
            "division_code": p["adm1_pcode"],
            "division_name": p["adm1_name"],
            "lat": round(float(p["center_lat"]), 6),
            "lon": round(float(p["center_lon"]), 6),
            "area_km2": round(float(p["area_sqkm"]), 2),
        })
    df = pd.DataFrame(rows).sort_values("district_code").reset_index(drop=True)
    n_dist, n_div = df.district_code.nunique(), df.division_code.nunique()
    if (n_dist, n_div, len(df)) != (EXPECTED_DISTRICTS, EXPECTED_DIVISIONS, EXPECTED_DISTRICTS):
        raise ValueError(f"expected 64 districts in 8 divisions, got {n_dist} in {n_div} ({len(df)} rows)")
    if df[["lat", "lon"]].isna().any().any():
        raise ValueError("null centroid in COD-AB ADM2")
    return df


def build_topojson(geo, out=TOPO_OUT, max_bytes=TOPO_MAX_BYTES):
    """Simplify with mapshaper (Visvalingam, keep-shapes so no small district vanishes) to TopoJSON.

    Tries progressively stronger simplification until the file is under `max_bytes`.
    """
    if not shutil.which("npx"):
        raise RuntimeError("npx not found; install Node.js (mise) to build the TopoJSON")
    slim = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": f["geometry"],
         "properties": {"district_code": f["properties"]["adm2_pcode"],
                        "district_name": f["properties"]["adm2_name"]}}
        for f in geo["features"]]}
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "districts.geojson"
        src.write_text(json.dumps(slim))
        for pct in ("5%", "3%", "2%", "1.2%", "0.8%", "0.5%"):
            tmp = pathlib.Path(td) / "districts.topo.json"
            cmd = ["npx", "-y", MAPSHAPER, "-i", str(src), "name=districts",
                   "-simplify", "visvalingam", pct, "keep-shapes",
                   "-o", "format=topojson", "quantization=100000", "precision=0.0001", str(tmp)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            size = tmp.stat().st_size
            print(f"  topojson at {pct}: {size / 1000:.0f} KB", flush=True)
            if size < max_bytes:
                shutil.copyfile(tmp, out)
                return out
    raise RuntimeError(f"could not simplify TopoJSON under {max_bytes} bytes")


def topo_codes(path=TOPO_OUT):
    topo = json.loads(pathlib.Path(path).read_text())
    return [g["properties"]["district_code"] for g in topo["objects"]["districts"]["geometries"]]
