#!/usr/bin/env python3
import base64, zlib, pathlib
root = pathlib.Path(__file__).resolve().parent
parts_dir = root / "src_parts"
blobs = []
for p in sorted(parts_dir.glob("cq.b64.*")):
    blobs.append(p.read_text().strip())
data = zlib.decompress(base64.b64decode("".join(blobs)))
target = root / "cq.py"
target.write_bytes(data)
print("wrote", target, target.stat().st_size)
