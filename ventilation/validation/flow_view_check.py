"""Check that the tracers and the speed slice show the solved flow faithfully.

Usage: flow_view_check.py <scene.usd>
Run with Kit's Python, with Warp, numpy, and pxr on PYTHONPATH.
"""

import os
import sys

import numpy as np
import warp as wp
from pxr import Usd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "source", "extensions", "adam.ventilation.cfd"))

from adam.ventilation.cfd.flow_view import SPEED_RANGE, TRAIL_LENGTH, FlowView  # noqa: E402
from adam.ventilation.cfd.scene import read_domain  # noqa: E402
from adam.ventilation.cfd.simulation import VentilationSimulation  # noqa: E402


def check(name, passed, detail=""):
    print(f"{'PASS' if passed else 'FAIL'}  {name}  {detail}")
    return passed


def grid_positions(view):
    points = view._trails.numpy().reshape(view.tracer_count, TRAIL_LENGTH, 3)
    return points[:, 0], points


def uniform_drift(domain):
    """A known uniform field must move a tracer exactly velocity * time."""
    sim = VentilationSimulation(domain)
    view = FlowView(sim, tracer_count=500)
    view.set_slice(1, 0.5)
    velocity = np.array((0.02, 0.0, 0.0), dtype=np.float32)
    sim.solver.u_field.fill_(wp.vec3(*velocity))
    view._ages.fill_(1e9)
    before, _ = grid_positions(view)
    view.capture(10)
    after, trails = grid_positions(view)

    survivors = np.linalg.norm(trails[:, 1] - before, axis=1) < 1e-6
    error = np.abs(after[survivors] - before[survivors] - velocity * 10).max()
    return check("uniform field moves tracers exactly", survivors.sum() > 400 and error < 1e-3, f"max error {error:.1e} cells")


def closed_vents(domain):
    """With everything shut, no tracer may be released at a vent."""
    sim = VentilationSimulation(domain)
    for vent in domain.vents:
        sim.set_all_slats(vent.name, 0.0)
    view = FlowView(sim, tracer_count=2000)
    view.set_slice(1, 0.5)
    view._ages.zero_()
    view.capture(2)
    heads, _ = grid_positions(view)
    seeds = view._seeds.numpy()
    nearest = np.abs(heads[:, None, :] - seeds[None, ::7, :] - 0.5).max(axis=2).min(axis=1)
    share = float((nearest < 0.5).mean())
    return check("closed vents release nothing", share < 0.02, f"{share:.1%} of tracers near a vent")


def live_flow(domain):
    sim = VentilationSimulation(domain)
    sim.set_all_slats("WallVent", 60.0)
    view = FlowView(sim)
    view.set_slice(1, 0.5)

    ok = True
    outside = 0
    steps = 30
    for _ in range(int(40.0 / (steps * sim.dt))):
        sim.advance(steps)
        frame = view.capture(steps)
        heads = np.floor(view._trails.numpy()[::TRAIL_LENGTH]).astype(int)
        outside += int((domain.flags[tuple(heads.T)] != 0).sum())
    ok &= check("tracers stay in the air", outside == 0, f"{outside} head positions outside fluid over 40 s")

    # Compare the tracer speed with the jet: streaks near a vent must be fast.
    fields = sim.velocity()
    speed = np.linalg.norm(fields, axis=-1)
    heads = np.floor(view._trails.numpy()[::TRAIL_LENGTH]).astype(int)
    tracer_speed = speed[tuple(heads.T)]
    ok &= check("tracers sample moving air", tracer_speed.mean() > 0.05, f"mean {tracer_speed.mean():.2f} m/s")

    # Share of tracers in the fastest 1 % of the room. Tracers released at the
    # vents should sit in the jets far more often than uniform seeding would.
    threshold = np.quantile(speed[domain.flags == 0], 0.99)
    in_jets = float((tracer_speed > threshold).mean())
    ok &= check("tracers concentrate in jets", in_jets > 0.02, f"{in_jets:.1%} in the fastest 1% of cells")

    # The slice colors must match an independent lookup from the velocity field.
    speed_slice = view.speed_slice
    colors = view.slice_colors(speed_slice)
    flat = speed.ravel(order="F")[speed_slice.cells.numpy()]
    fastest = np.argmax(flat)
    slowest = np.argmin(flat)
    palette = view._palette.numpy()
    expected_top = palette[-1] if flat[fastest] >= SPEED_RANGE else None
    ok &= check("slice shows zero speed as the low color", np.allclose(colors[slowest], palette[0], atol=1e-2))
    if expected_top is not None:
        ok &= check("slice saturates at the top color", np.allclose(colors[fastest], expected_top, atol=1e-3))
    middle = np.argmin(np.abs(flat - SPEED_RANGE * 0.5))
    ok &= check(
        "slice is linear in speed",
        np.allclose(colors[middle], palette[len(palette) // 2], atol=0.1),
        f"{flat[middle]:.2f} m/s gives {colors[middle].round(2)}, expected {palette[len(palette) // 2].round(2)}",
    )

    faces = len(speed_slice.face_indices) // 4
    ok &= check("slice has no faces over solids", faces > 0 and domain.flags.ravel(order="F")[speed_slice.cells.numpy()][speed_slice.face_indices].max() <= 2, f"{faces} faces")
    return ok


def main():
    domain = read_domain(Usd.Stage.Open(sys.argv[1]), 0.05)
    results = [uniform_drift(domain), closed_vents(domain), live_flow(domain)]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
