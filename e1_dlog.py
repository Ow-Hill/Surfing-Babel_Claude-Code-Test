import time
from gmpy2 import mpz
L=lambda n: mpz(open(f"e1/{n}.loc").read().strip())
N=mpz(open("e1/N_candidate.txt").read()); L0=L("black")
A=(L("p00_B1")-L0)%N
targets={(L(n)-L0)%N: n for n in ("plast_R1","pmid_R1","pblock_B1","p00_R1","p01_R1","p10_R1")}
t=mpz(A); t0=time.time()
for e in range(0, 800_000):
    if t in targets:
        print(f"{targets[t]}: digit position {e}", flush=True)
    t = t << 4
    while t >= N:
        t -= N
    if e % 100_000 == 0:
        print(f"  ...{e} ({time.time()-t0:.0f}s)", flush=True)
print("done", flush=True)
