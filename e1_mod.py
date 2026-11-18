import sys, time, gmpy2
from gmpy2 import mpz
L={n:mpz(open(f"e1/{n}.loc").read().strip()) for n in ["black","p00_R1","p00_G1","p00_B1","p10_R1","p01_R1"]}
K={n:L[n]-L["black"] for n in L if n!="black"}
cands={f"16^{m}":mpz(16)**m for m in (798720,798721,798722,798723,798724,798725)}
cands.update({"10^961760":mpz(10)**961760,"10^961759":mpz(10)**961759})
for name,N in cands.items():
    t=time.time()
    try: inv=gmpy2.invert(K["p00_R1"] % N, N)
    except ZeroDivisionError: print(name,"not invertible",flush=True); continue
    out=[]
    for n in ("p00_G1","p00_B1","p10_R1","p01_R1"):
        r=(K[n]*inv) % N; small=min(r, N-r)
        out.append(f"{n}: {small.bit_length()} bits" + (" POW2" if small & (small-1) == 0 else ""))
    print(name, "|", " | ".join(out), f"({time.time()-t:.0f}s)", flush=True)
