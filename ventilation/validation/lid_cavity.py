"""Validate the LBM solver against the Ghia et al. (1982) lid-driven cavity.

A 3:1 span cavity approximates the 2D benchmark on its mid-plane. Smagorinsky
is off, so this checks streaming, collision, and moving-wall boundaries only.

Run with Kit's Python, with Warp and numpy on PYTHONPATH.
"""

import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "source", "extensions", "adam.ventilation.cfd"))

from adam.ventilation.cfd import solver as lbm  # noqa: E402

RE = 100.0
N = 64
SPAN = 3 * N
LID_SPEED = 0.1
STEPS = 30000

# Ghia, Ghia & Shin 1982, Table I: u on the vertical centerline, Re = 100.
GHIA_Y = np.array([0.0547, 0.0625, 0.0703, 0.1016, 0.1719, 0.2813, 0.4531, 0.5, 0.6172, 0.7344, 0.8516, 0.9531, 0.9609, 0.9688, 0.9766])
GHIA_U = np.array([-0.03717, -0.04192, -0.04775, -0.06434, -0.10150, -0.15662, -0.21090, -0.20581, -0.13641, 0.00332, 0.23151, 0.68717, 0.73722, 0.78871, 0.84123])


def main():
    # Cavity interior spans cells 1..N in x and z. The top row is the moving lid.
    flags = np.full((N + 2, SPAN + 2, N + 2), lbm.FLAG_SOLID, dtype=np.int32)
    flags[1:-1, 1:-1, 1:-1] = lbm.FLAG_FLUID
    flags[1:-1, 1:-1, -1] = lbm.FLAG_INLET

    tau0 = 0.5 + 3.0 * LID_SPEED * N / RE
    sim = lbm.LbmSolver(flags, 1, tau0, smagorinsky=0.0)
    sim.set_boundary_velocity([[LID_SPEED, 0.0, 0.0]])
    sim.set_boundary_normal([[0.0, 0.0, -1.0]])

    start = time.time()
    previous = None
    for _ in range(STEPS // 1000):
        sim.step(1000)
        u = sim.velocity()[(N + 2) // 2, (SPAN + 2) // 2, 1:-1, 0] / LID_SPEED
        change = None if previous is None else float(np.abs(u - previous).max())
        previous = u
    elapsed = time.time() - start

    z = (np.arange(N) + 0.5) / N
    model = np.interp(GHIA_Y, z, u)
    error = model - GHIA_U
    print(f"{STEPS} steps in {elapsed:.1f}s, last change per 1000 steps {change:.2e}")
    print("    y     Ghia     model")
    for row in zip(GHIA_Y, GHIA_U, model):
        print(f"{row[0]:.4f}  {row[1]:+.4f}  {row[2]:+.4f}")
    print(f"RMS error {np.sqrt((error ** 2).mean()):.4f}, max error {np.abs(error).max():.4f} (units of lid speed)")


if __name__ == "__main__":
    main()
