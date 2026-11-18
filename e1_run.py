"""E1: designed uploads to Babelia's image search. One at a time, 65 s apart."""
import io, json, sys, time
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, "/home/user/Surfing-Babel_Claude-Code-Test")
from babel_surfer.seeded import search_location
from babel_surfer.scan import fetch
OUT = Path(sys.argv[1]); OUT.mkdir(exist_ok=True)
H, W = 416, 640
def img(changes):
    a = np.zeros((H, W, 3), np.uint8)
    for (x, y, c, lvl) in changes:
        a[y, x, c] = lvl * 17
    return a
CASES = [
    ("black", []),
    ("p00_R1", [(0, 0, 0, 1)]), ("p00_G1", [(0, 0, 1, 1)]), ("p00_B1", [(0, 0, 2, 1)]),
    ("p00_R2", [(0, 0, 0, 2)]), ("p10_R1", [(1, 0, 0, 1)]), ("p01_R1", [(0, 1, 0, 1)]),
    ("p00_R1+p10_R1", [(0, 0, 0, 1), (1, 0, 0, 1)]),
    ("plast_R1", [(639, 415, 0, 1)]), ("pmid_R1", [(320, 208, 0, 1)]),
    ("pblock_B1", [(480, 384, 2, 1)]),
]
done = {p.stem for p in OUT.glob("*.loc")}
for name, ch in CASES:
    if name in done:
        continue
    a = img(ch)
    buf = io.BytesIO(); Image.fromarray(a).save(buf, "PNG")
    np.save(OUT / f"{name}.npy", a)
    t = time.time()
    try:
        loc = search_location(buf.getvalue(), f"{name}.png")
    except Exception as e:
        print(name, "ERROR", e, flush=True); break
    (OUT / f"{name}.loc").write_text(loc)
    print(name, len(loc), "digits", round(time.time() - t, 1), "s", flush=True)
    time.sleep(65)
# round trip: the frames at two returned locations must reproduce the uploads
for name in ("black", "p00_R1"):
    f = OUT / f"{name}.jpg"
    if f.exists() or not (OUT / f"{name}.loc").exists():
        continue
    data = fetch((OUT / f"{name}.loc").read_text(), timeout=180)
    f.write_bytes(data)
    rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), float)
    lv = np.clip(np.rint(rgb / 17), 0, 15).astype(int)
    want = np.load(OUT / f"{name}.npy").astype(int) // 17
    print(name, "round trip: values matching", round(float((lv == want).mean()), 5), flush=True)
    time.sleep(65)
print("DONE", flush=True)
