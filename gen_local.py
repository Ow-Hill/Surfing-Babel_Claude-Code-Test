import sys, time
import gmpy2
from gmpy2 import mpz
import numpy as np
from PIL import Image
L=lambda n: mpz(open(f"e1/{n}.loc").read().strip())
N=mpz(open("e1/N_candidate.txt").read()); L0=L("black")
A=(L("p00_B1")-L0)%N                                  # the constant for digit 0 (blue at 0,0)
H, W, STRIP = 416, 640, 160
def pos(x, y, c):                                     # digit index: strips of 160 px, rows, x, B<G<R
    s, xl = divmod(x, STRIP)
    return s*H*STRIP*3 + y*STRIP*3 + xl*3 + {2:0, 1:1, 0:2}[c]
# 1. the far positions fit the same formula
for name,(x,y,c) in {"plast_R1":(639,415,0),"pmid_R1":(320,208,0),"pblock_B1":(480,384,2)}.items():
    K=(L(name)-L0)%N
    print(name, "K == A*16^pos:", K == A*pow(mpz(16), pos(x,y,c), N) % N, flush=True)
# 2. generate frames locally and compare with Babelia's
Ainv=gmpy2.invert(A, N)
order=np.empty(H*W*3, np.int64)                        # digit index -> flat (y, x, channel)
for y in range(H):
    for x in range(W):
        for c in range(3):
            order[pos(x,y,c)] = (y*W + x)*3 + c
def frame(loc):
    X = (Ainv*(mpz(loc)-L0)) % N
    h = gmpy2.digits(X, 16).rjust(H*W*3, "0")
    d = np.frombuffer(h[::-1].encode(), np.uint8)       # least significant digit first
    d = np.where(d >= 97, d - 87, d - 48)[:H*W*3]
    img = np.empty(H*W*3, np.uint8); img[order] = d
    return img.reshape(H, W, 3), len(h)
for loc in (1000000000, 1000000001, 7345678901234, 4503599627370000):
    t=time.time(); f, nd = frame(loc)
    real = np.clip(np.rint(np.asarray(Image.open(f"gen/{loc}.jpg").convert("RGB"), float)/17), 0, 15)
    print(f"location {loc}: digits {nd}, values matching Babelia's frame {float((f==real).mean()):.4f}  ({time.time()-t:.1f}s)", flush=True)
