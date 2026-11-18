import io, sys, time
import numpy as np
from PIL import Image
from gmpy2 import mpz
sys.path.insert(0, "/home/user/Surfing-Babel_Claude-Code-Test")
from babel_surfer.seeded import search_location
L = lambda n: mpz(open(f"e1/{n}.loc").read().strip())
N = mpz(open("e1/N_candidate.txt").read())
L0 = L("black"); K = {n: L(n) - L0 for n in ("p00_R1", "p00_G1", "p00_B1", "p01_R1", "p10_R1")}
pred = (L0 + sum(K.values())) % N
a = np.zeros((416, 640, 3), np.uint8)
a[0, 0] = (17, 17, 17); a[1, 0, 0] = 17; a[0, 1, 0] = 17
buf = io.BytesIO(); Image.fromarray(a).save(buf, "PNG")
got = mpz(search_location(buf.getvalue(), "combo.png"))
open("e1/combo.loc", "w").write(str(got))
print("predicted == returned:", pred == got)
if pred != got:
    d = got - pred
    print("difference digits", len(str(abs(d))), "difference / N =", float(d) / float(N) if d else 0)
