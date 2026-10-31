"""Run the garage CFD headless and save speed slices as a PNG.

Usage: garage_run.py <scene.usd> <output.png> [simulated_seconds]
Run with Kit's Python, with Warp, numpy, Pillow, and pxr on PYTHONPATH.
"""

import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw
from pxr import Usd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "source", "extensions", "adam.ventilation.cfd"))

from adam.ventilation.cfd.scene import read_domain  # noqa: E402
from adam.ventilation.cfd.simulation import VentilationSimulation  # noqa: E402

PIXELS_PER_CELL = 6
SPEED_RANGE = 1.5  # m/s at the top of the color scale


def colorize(speed, solid):
    t = np.clip(speed / SPEED_RANGE, 0.0, 1.0)[..., None]
    stops = np.array([[20, 24, 60], [30, 110, 190], [40, 190, 170], [240, 220, 70], [230, 60, 40]], dtype=float)
    position = t[..., 0] * (len(stops) - 1)
    low = np.clip(position.astype(int), 0, len(stops) - 2)
    fraction = (position - low)[..., None]
    rgb = stops[low] * (1 - fraction) + stops[low + 1] * fraction
    rgb[solid] = (70, 70, 74)
    return rgb.astype(np.uint8)


def panel(speed, solid, title):
    """Render a (horizontal, vertical) slice with the vertical axis pointing up."""
    rgb = np.flipud(colorize(speed, solid).transpose(1, 0, 2))
    image = Image.fromarray(rgb).resize((rgb.shape[1] * PIXELS_PER_CELL, rgb.shape[0] * PIXELS_PER_CELL), Image.NEAREST)
    canvas = Image.new("RGB", (image.width, image.height + 22), (15, 15, 18))
    canvas.paste(image, (0, 22))
    ImageDraw.Draw(canvas).text((4, 5), title, fill=(235, 235, 235))
    return canvas


def main():
    scene, output = sys.argv[1], sys.argv[2]
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

    start = time.time()
    domain = read_domain(Usd.Stage.Open(scene), 0.05)
    flags = domain.flags
    print(f"grid {flags.shape} = {flags.size / 1e6:.2f}M cells, fluid {domain.fluid_volume:.1f} m^3, voxelized in {time.time() - start:.1f}s")
    print(f"car {domain.car_cells * domain.dx ** 3:.2f} m^3, door cells {(flags == 2).sum()}")
    for vent in domain.vents:
        print(f"{vent.name}: normal {vent.normal.round(2)}, slat cells {vent.slat_cells}, saved angles {vent.slat_angles}")

    sim = VentilationSimulation(domain)
    print(f"dt {sim.dt * 1000:.3f} ms, tau0 {sim.solver.tau0:.7f}")
    sim.set_all_slats("WallVent", 60.0)

    steps = int(duration / sim.dt)
    chunk = max(steps // 10, 1)
    start = time.time()
    for _ in range(10):
        sim.advance(chunk)
        m = sim.metrics()
        print(
            f"t={m['time']:5.1f}s supply {m['supply']:.3f} door {m['door_flow']:.3f} m3/s  "
            f"ACH {m['air_changes']:.1f}  mean {m['mean_speed']:.3f} max {m['max_speed']:.2f} m/s  stable {m['stable']}"
        )
        if not m["stable"]:
            break
    elapsed = time.time() - start
    print(f"{sim.solver.steps} steps in {elapsed:.1f}s = {sim.solver.steps / elapsed:.0f} steps/s, {sim.time / elapsed:.2f}x real time")

    speed = np.linalg.norm(sim.velocity(), axis=-1)
    solid = (flags != 0) & (flags != 2)
    origin = domain.origin
    j = int((0.8 - origin[1]) / domain.dx)  # plane through the ceiling vent
    jw = int((0.0 - origin[1]) / domain.dx)  # plane through the wall vent
    k = int((1.0 - origin[2]) / domain.dx)  # horizontal plane at 1 m
    panels = [
        panel(speed[:, j, :], solid[:, j, :], "x-z slice at y=0.8 m, through ceiling vent"),
        panel(speed[:, jw, :], solid[:, jw, :], "x-z slice at y=0, through wall vent"),
        panel(speed[:, :, k], solid[:, :, k], f"x-y plan at z=1 m, door at bottom. color 0-{SPEED_RANGE} m/s"),
    ]
    sheet = Image.new("RGB", (max(p.width for p in panels), sum(p.height for p in panels)), (15, 15, 18))
    y = 0
    for image in panels:
        sheet.paste(image, (0, y))
        y += image.height
    sheet.save(output)
    print("saved", output)


if __name__ == "__main__":
    main()
