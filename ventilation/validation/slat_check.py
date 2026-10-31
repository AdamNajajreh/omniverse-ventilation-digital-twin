"""Check that every slat controls its own part of the vent.

Usage: slat_check.py <scene.usd>
Run with Kit's Python, with Warp, numpy, and pxr on PYTHONPATH.
"""

import os
import sys

import numpy as np
from pxr import Usd, UsdGeom

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "source", "extensions", "adam.ventilation.cfd"))

from adam.ventilation.cfd.scene import read_domain  # noqa: E402
from adam.ventilation.cfd.simulation import VentilationSimulation  # noqa: E402


def check(name, passed, detail=""):
    print(f"{'PASS' if passed else 'FAIL'}  {name}  {detail}")
    return passed


def strip_mapping(stage, domain):
    """The strip that a slat drives must be the one under the physical blade."""
    ok = True
    cache = UsdGeom.XformCache()
    for vent in domain.vents:
        count = len(vent.slat_paths)
        for slat, path in enumerate(vent.slat_paths):
            position = np.array(cache.GetLocalToWorldTransform(stage.GetPrimAtPath(path)).ExtractTranslation())
            across = float((position - vent.center) @ vent.stack)
            ok &= int((across + vent.height / 2) / (vent.height / count)) == slat
    return check("each slat drives the strip under its blade", ok)


def equal_flow(domain):
    sim = VentilationSimulation(domain)
    for vent in domain.vents:
        sim.set_all_slats(vent.name, 90.0)
    flows = sim.metrics()["slat_flow"]
    ok = True
    for vent in domain.vents:
        expected = sim.fans[vent.name] * sim.config.fan_speed_max * vent.width * vent.height / len(vent.slat_angles)
        error = max(abs(flow - expected) / expected for flow in flows[vent.name])
        ok &= check(f"{vent.name} slats carry equal flow", error < 1e-5, f"{np.round(flows[vent.name], 3)} expected {expected:.3f}")
    return ok


def face_speeds(domain, closed):
    """Time-averaged outflow in the first air cell in front of each wall vent slat."""
    vent = next(v for v in domain.vents if v.name == "WallVent")
    sim = VentilationSimulation(domain)
    sim.set_all_slats("CeilingVent", 0.0)
    sim.set_all_slats("WallVent", 90.0)
    if closed is not None:
        sim.set_slat("WallVent", closed, 0.0)
    sim.advance(int(5.0 / sim.dt))

    samples = 40
    average = 0.0
    for _ in range(samples):
        sim.advance(int(0.05 / sim.dt))
        average = average + sim.velocity() @ vent.normal / samples

    first = 3 + vent.first_boundary
    ahead = np.rint(vent.normal).astype(int)
    return [float(average[tuple((np.argwhere(domain.flags == first + s) + ahead).T)].mean()) for s in range(len(vent.slat_angles))]


def isolated_jet(domain):
    """Closing a slat must cut the jet at its own position, not at another slat.

    Measured at the face: jets from strips this narrow merge within about 10 cm.
    """
    baseline = face_speeds(domain, None)
    print("      all open:", np.round(baseline, 2), "m/s")
    ok = True
    for closed in (1, 2, 4):
        speeds = face_speeds(domain, closed)
        drops = np.array(baseline) - speeds
        print(f"      slat {closed} closed:", np.round(speeds, 2), "m/s")
        ok &= speeds[closed] < 0.5 * baseline[closed] and int(np.argmax(drops)) == closed
    return check("closing a slat cuts the jet at that slat", ok)


def full_power(domain):
    """The area correction speeds up some strips, so check the hardest case."""
    sim = VentilationSimulation(domain)
    for vent in domain.vents:
        sim.set_fan(vent.name, 1.0)
        sim.set_all_slats(vent.name, 90.0)
    print(f"      fastest inlet is {sim.jet_speed:.3f} in lattice units")
    sim.advance(int(30.0 / sim.dt))
    metrics = sim.metrics()
    ok = check("stable at 100% fan with all slats open", metrics["stable"] and metrics["max_speed"] < 6.0, f"max {metrics['max_speed']:.2f} m/s")

    door = []
    for _ in range(20):
        sim.advance(int(1.0 / sim.dt))
        door.append(sim.metrics()["door_flow"])
    supply = sim.metrics()["supply"]
    error = (np.mean(door) - supply) / supply
    return check("door flow matches supply", ok and abs(error) < 0.05, f"door {np.mean(door):.3f}, supply {supply:.3f} m3/s, {error:+.1%}")


def mixed_balance(domain):
    """Mixed tilts must not create or lose air: inward tilts at both rims are the hard case."""
    sim = VentilationSimulation(domain)
    for slat, angle in enumerate((20.0, 45.0, 90.0, 135.0, 160.0)):
        sim.set_slat("WallVent", slat, angle)
    sim.set_slat("CeilingVent", 2, 0.0)
    sim.advance(int(30.0 / sim.dt))

    door = []
    for _ in range(30):
        sim.advance(int(1.0 / sim.dt))
        door.append(sim.metrics()["door_flow"])
    supply = sim.metrics()["supply"]
    error = (np.mean(door) - supply) / supply
    return check("door flow matches supply with mixed tilts", abs(error) < 0.03, f"door {np.mean(door):.3f}, supply {supply:.3f} m3/s, {error:+.1%}")


def main():
    stage = Usd.Stage.Open(sys.argv[1])
    domain = read_domain(stage, 0.05)
    results = [strip_mapping(stage, domain), equal_flow(domain), isolated_jet(domain), full_power(domain), mixed_balance(domain)]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
