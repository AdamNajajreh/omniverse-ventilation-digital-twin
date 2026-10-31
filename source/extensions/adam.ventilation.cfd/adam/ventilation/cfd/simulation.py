"""Physical-unit wrapper around the LBM solver: fans, slats, and flow metrics."""

import math
from dataclasses import dataclass

import numpy as np

from .solver import LbmSolver


@dataclass
class CfdConfig:
    cell_size: float = 0.05  # m
    fan_speed_max: float = 3.0  # m/s, jet speed through the slats at 100 % fan
    lattice_speed_max: float = 0.1  # keeps the Mach number low enough for LBM
    air_viscosity: float = 1.5e-5  # m^2/s, at 20 C
    smagorinsky: float = 0.15
    closed_angle: float = 5.0  # degrees, slats below this seal the vent
    still_air_speed: float = 0.05  # m/s, below this the air counts as still


class VentilationSimulation:
    """Runs the airflow for a voxelized garage.

    Slats are far thinner than a cell, so they act as a boundary condition
    instead of geometry. Each slat owns one strip of the vent, and air leaves
    that strip along the blade chord at the fan speed. With slat angle ``a`` the
    velocity is ``speed * (stack * cos(a) + normal * sin(a))``. The normal part
    gives flow proportional to the free area ``sin(a)``, and the whole vector
    carries the right momentum for the jet.
    """

    def __init__(self, domain, config=None, device=None):
        self.domain = domain
        self.config = config or CfdConfig()

        # The fastest jet maps to lattice_speed_max, which fixes the time step.
        self.dt = self.config.lattice_speed_max * domain.dx / self.config.fan_speed_max
        self.velocity_scale = domain.dx / self.dt  # lattice to m/s
        tau0 = 0.5 + 3.0 * self.config.air_viscosity * self.dt / domain.dx**2

        self.solver = LbmSolver(domain.flags, domain.boundary_count, tau0, self.config.smagorinsky, device)
        self.fans = {vent.name: 0.5 for vent in domain.vents}

        normals = np.zeros((max(domain.boundary_count, 1), 3))
        for vent in domain.vents:
            normals[vent.first_boundary : vent.first_boundary + len(vent.slat_angles)] = vent.normal
        self.solver.set_boundary_normal(normals)
        self._apply_boundaries()

    @property
    def time(self):
        return self.solver.steps * self.dt

    def _vent(self, name):
        return next(vent for vent in self.domain.vents if vent.name == name)

    def _slat_velocity(self, vent, slat):
        """Return the world-space jet velocity of a slat in m/s."""
        angle = vent.slat_angles[slat]
        if not self.config.closed_angle < angle < 180.0 - self.config.closed_angle:
            return np.zeros(3)
        radians = math.radians(angle)
        speed = self.fans[vent.name] * self.config.fan_speed_max

        # A slat rarely covers a whole number of cell rows. Scaling by true area
        # over grid area keeps the flow of every slat equal.
        slat_area = vent.width * vent.height / len(vent.slat_angles)
        speed *= slat_area / (vent.slat_cells[slat] * self.domain.dx**2)
        return speed * (vent.stack * math.cos(radians) + vent.normal * math.sin(radians))

    def _apply_boundaries(self):
        velocities = np.zeros((max(self.domain.boundary_count, 1), 3))
        for vent in self.domain.vents:
            for slat in range(len(vent.slat_angles)):
                velocities[vent.first_boundary + slat] = self._slat_velocity(vent, slat) / self.velocity_scale
        self.solver.set_boundary_velocity(velocities)
        self.jet_speed = float(np.linalg.norm(velocities, axis=1).max())  # lattice units

    def set_fan(self, vent_name, fraction):
        """``fraction`` runs from 0 (off) to 1 (full speed)."""
        self.fans[vent_name] = float(np.clip(fraction, 0.0, 1.0))
        self._apply_boundaries()

    def set_slat(self, vent_name, slat, angle):
        """0 degrees is closed, 90 points straight out, 180 is closed again."""
        self._vent(vent_name).slat_angles[slat] = float(np.clip(angle, 0.0, 180.0))
        self._apply_boundaries()

    def set_all_slats(self, vent_name, angle):
        vent = self._vent(vent_name)
        vent.slat_angles[:] = [float(np.clip(angle, 0.0, 180.0))] * len(vent.slat_angles)
        self._apply_boundaries()

    def reset(self):
        self.solver.reset()

    def advance(self, steps):
        self.solver.step(steps)

    def advance_batch(self, steps):
        self.solver.step_batch(steps)

    def velocity(self):
        """Return the velocity field in m/s, with shape (nx, ny, nz, 3)."""
        return self.solver.velocity() * self.velocity_scale

    def metrics(self):
        velocity = self.velocity()
        speed = np.linalg.norm(velocity, axis=-1)
        fluid = self.domain.flags == 0
        area = self.domain.dx**2

        slat_flow = {
            vent.name: [
                float(self._slat_velocity(vent, slat) @ vent.normal) * cells * area
                for slat, cells in enumerate(vent.slat_cells)
            ]
            for vent in self.domain.vents
        }
        supply = sum(sum(flows) for flows in slat_flow.values())

        # Net flow out through the first fluid row inside the door.
        door_row = fluid[:, 1, :]
        door_flow = -float(velocity[:, 1, :, 1][door_row].sum()) * area

        return {
            "time": self.time,
            "supply": supply,
            "slat_flow": slat_flow,
            "door_flow": door_flow,
            "air_changes": supply / self.domain.fluid_volume * 3600.0,
            "mean_speed": float(speed[fluid].mean()),
            "max_speed": float(speed[fluid].max()),
            "still_air": float((speed[fluid] < self.config.still_air_speed).mean()),
            "stable": bool(np.isfinite(speed[fluid]).all()),
        }
