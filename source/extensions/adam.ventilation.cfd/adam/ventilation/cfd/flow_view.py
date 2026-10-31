"""Turn the live velocity field into tracer streaks and a colored speed slice.

Everything here runs on the GPU and knows nothing about USD. Positions are in
grid coordinates: one unit is one cell, and cell ``i`` spans ``[i, i + 1)``.
Lattice velocity is in cells per step, so moving a tracer needs no conversion.
"""

from dataclasses import dataclass

import numpy as np
import warp as wp

from .solver import FLAG_FLUID, FLAG_INLET, FLAG_OUTLET

wp.set_module_options({"enable_backward": False})

TRACER_COUNT = 4000
TRAIL_LENGTH = 8  # points per streak, one per capture
VENT_SHARE = 0.7  # share of new tracers released at the vents, the rest anywhere
TRACER_LIFE = (8.0, 25.0)  # seconds
SPEED_RANGE = 1.5  # m/s at the top of the color scale
PALETTE = ((20, 24, 60), (30, 110, 190), (40, 190, 170), (240, 220, 70), (230, 60, 40))  # sRGB


@wp.func
def sample_velocity(u_field: wp.array(dtype=wp.vec3), p: wp.vec3, nx: int, ny: int, nz: int) -> wp.vec3:
    """Trilinear interpolation. Velocities live at the cell centers."""
    x = wp.clamp(p[0] - 0.5, 0.0, float(nx) - 1.001)
    y = wp.clamp(p[1] - 0.5, 0.0, float(ny) - 1.001)
    z = wp.clamp(p[2] - 0.5, 0.0, float(nz) - 1.001)
    i = int(x)
    j = int(y)
    k = int(z)
    fx = x - float(i)
    fy = y - float(j)
    fz = z - float(k)

    c = i + nx * (j + ny * k)
    up = nx * ny
    near = wp.lerp(wp.lerp(u_field[c], u_field[c + 1], fx), wp.lerp(u_field[c + nx], u_field[c + nx + 1], fx), fy)
    far = wp.lerp(
        wp.lerp(u_field[c + up], u_field[c + up + 1], fx),
        wp.lerp(u_field[c + up + nx], u_field[c + up + nx + 1], fx),
        fy,
    )
    return wp.lerp(near, far, fz)


@wp.func
def speed_color(speed: float, scale: float, palette: wp.array(dtype=wp.vec3)) -> wp.vec3:
    position = wp.clamp(speed * scale, 0.0, 1.0) * float(palette.shape[0] - 1)
    low = wp.min(int(position), palette.shape[0] - 2)
    return wp.lerp(palette[low], palette[low + 1], position - float(low))


@wp.kernel
def advect_tracers(
    trails: wp.array(dtype=wp.vec3),
    colors: wp.array(dtype=wp.vec3),
    ages: wp.array(dtype=float),
    u_field: wp.array(dtype=wp.vec3),
    flags: wp.array(dtype=int),
    fluid_cells: wp.array(dtype=int),
    seeds: wp.array(dtype=wp.vec3),
    seed_boundaries: wp.array(dtype=int),
    boundary_velocity: wp.array(dtype=wp.vec3),
    palette: wp.array(dtype=wp.vec3),
    nx: int,
    ny: int,
    nz: int,
    trail_length: int,
    steps: float,
    frame: int,
    speed_scale: float,
    jet_speed: float,
    vent_share: float,
    life_steps_min: float,
    life_steps_max: float,
):
    t = wp.tid()
    head = t * trail_length

    # Second-order midpoint step.
    p = trails[head]
    half = p + sample_velocity(u_field, p, nx, ny, nz) * 0.5 * steps
    moved = p + sample_velocity(u_field, half, nx, ny, nz) * steps
    age = ages[t] - steps

    i = wp.clamp(int(wp.floor(moved[0])), 0, nx - 1)
    j = wp.clamp(int(wp.floor(moved[1])), 0, ny - 1)
    k = wp.clamp(int(wp.floor(moved[2])), 0, nz - 1)
    alive = age > 0.0 and flags[i + nx * (j + ny * k)] == FLAG_FLUID

    if alive:
        ages[t] = age
        for s in range(trail_length - 1, 0, -1):
            trails[head + s] = trails[head + s - 1]
            colors[head + s] = colors[head + s - 1]
        trails[head] = moved
        colors[head] = speed_color(wp.length(sample_velocity(u_field, moved, nx, ny, nz)), speed_scale, palette)
    else:
        state = wp.rand_init(frame, t)
        jitter = wp.vec3(wp.randf(state), wp.randf(state), wp.randf(state))

        # Accepting a vent seed in proportion to its jet speed releases tracers
        # in proportion to flow, and a closed slat releases none.
        at_vent = False
        spawn = wp.vec3(0.0, 0.0, 0.0)
        if seeds.shape[0] > 0 and jet_speed > 0.0 and wp.randf(state) < vent_share:
            s = wp.randi(state, 0, seeds.shape[0])
            speed = wp.length(boundary_velocity[seed_boundaries[s]])
            if speed > wp.randf(state) * jet_speed:
                spawn = seeds[s] + jitter
                at_vent = True
        if not at_vent:
            c = fluid_cells[wp.randi(state, 0, fluid_cells.shape[0])]
            k_new = c // (nx * ny)
            j_new = (c - k_new * nx * ny) // nx
            i_new = c - (k_new * ny + j_new) * nx
            spawn = wp.vec3(float(i_new), float(j_new), float(k_new)) + jitter

        ages[t] = wp.lerp(life_steps_min, life_steps_max, wp.randf(state))
        color = speed_color(wp.length(sample_velocity(u_field, spawn, nx, ny, nz)), speed_scale, palette)
        for s in range(trail_length):
            trails[head + s] = spawn
            colors[head + s] = color


@wp.kernel
def sample_slice(
    colors: wp.array(dtype=wp.vec3),
    cells: wp.array(dtype=int),
    u_field: wp.array(dtype=wp.vec3),
    palette: wp.array(dtype=wp.vec3),
    speed_scale: float,
):
    t = wp.tid()
    colors[t] = speed_color(wp.length(u_field[cells[t]]), speed_scale, palette)


@dataclass(eq=False)
class SpeedSlice:
    """A plane of cell centers, meshed wherever four neighbors are all air."""

    axis: int  # the axis normal to the plane
    index: int
    points: np.ndarray  # (m, 3), world space
    face_indices: np.ndarray  # (4 * faces,)
    cells: wp.array  # flat grid index of each point
    colors: wp.array


@dataclass
class FlowFrame:
    tracer_points: np.ndarray  # (tracers * TRAIL_LENGTH, 3), world space
    tracer_colors: np.ndarray
    speed_slice: SpeedSlice
    slice_colors: np.ndarray


def _vent_seeds(domain):
    """Return the air cells in front of each slat, with their boundary indices."""
    flags = domain.flags
    seeds = []
    boundaries = []
    for vent in domain.vents:
        first = FLAG_INLET + vent.first_boundary
        inlets = np.argwhere((flags >= first) & (flags < first + len(vent.slat_angles)))
        ahead = inlets + np.rint(vent.normal).astype(int)
        open_air = flags[tuple(ahead.T)] == FLAG_FLUID
        seeds.append(ahead[open_air])
        boundaries.append(flags[tuple(inlets[open_air].T)] - FLAG_INLET)
    if not seeds:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int32)
    return np.concatenate(seeds), np.concatenate(boundaries)


def _srgb_to_linear(values):
    return (np.asarray(values, dtype=np.float32) / 255.0) ** 2.2


class FlowView:
    def __init__(self, sim, tracer_count=TRACER_COUNT, seed=1):
        self.sim = sim
        self.tracer_count = tracer_count
        self.speed_slice = None
        self._device = sim.solver.device
        self._frame = 0

        domain = sim.domain
        self._flat_flags = domain.flags.ravel(order="F")
        self._speed_scale = sim.velocity_scale / SPEED_RANGE
        self._life_steps = tuple(life / sim.dt for life in TRACER_LIFE)

        fluid_cells = np.flatnonzero(self._flat_flags == FLAG_FLUID).astype(np.int32)
        if len(fluid_cells) == 0:
            raise RuntimeError("The grid has no air cells")
        seeds, boundaries = _vent_seeds(domain)

        # Start spread through the room, with lives already partly used, so the
        # first tracers do not all return to the vents at the same moment.
        random = np.random.default_rng(seed)
        nx, ny, _ = domain.flags.shape
        picked = random.choice(fluid_cells, tracer_count)
        k, picked = np.divmod(picked, nx * ny)
        j, i = np.divmod(picked, nx)
        start = np.stack((i, j, k), axis=1) + random.random((tracer_count, 3))

        device = self._device
        self._trails = wp.array(np.repeat(start, TRAIL_LENGTH, axis=0), dtype=wp.vec3, device=device)
        self._tracer_colors = wp.zeros(tracer_count * TRAIL_LENGTH, dtype=wp.vec3, device=device)
        self._ages = wp.array(random.uniform(0.0, self._life_steps[1], tracer_count), dtype=float, device=device)
        self._fluid_cells = wp.array(fluid_cells, dtype=int, device=device)
        self._seeds = wp.array(seeds, dtype=wp.vec3, device=device)
        self._seed_boundaries = wp.array(boundaries.astype(np.int32), dtype=int, device=device)
        self._palette = wp.array(_srgb_to_linear(PALETTE), dtype=wp.vec3, device=device)

    def set_slice(self, axis, fraction):
        """Select the plane normal to ``axis``, ``fraction`` of the way along it."""
        flags = self.sim.domain.flags
        origin = self.sim.domain.origin
        dx = self.sim.domain.dx
        index = int(np.clip(fraction * flags.shape[axis], 0, flags.shape[axis] - 1))

        plane_axes = [a for a in range(3) if a != axis]
        first, second = (flags.shape[a] for a in plane_axes)
        grid = np.empty((first, second, 3), dtype=np.int64)
        grid[..., axis] = index
        grid[..., plane_axes[0]] = np.arange(first)[:, None]
        grid[..., plane_axes[1]] = np.arange(second)[None, :]

        air = np.isin(flags[tuple(np.moveaxis(grid, -1, 0))], (FLAG_FLUID, FLAG_OUTLET))
        corners = air[:-1, :-1] & air[1:, :-1] & air[1:, 1:] & air[:-1, 1:]
        a, b = np.nonzero(corners)
        quad = np.stack((a * second + b, (a + 1) * second + b, (a + 1) * second + b + 1, a * second + b + 1), axis=1)

        nx, ny, _ = flags.shape
        cells = (grid[..., 0] + nx * (grid[..., 1] + ny * grid[..., 2])).ravel()
        speed_slice = SpeedSlice(
            axis=axis,
            index=index,
            points=(origin + (grid.reshape(-1, 3) + 0.5) * dx).astype(np.float32),
            face_indices=quad.ravel().astype(np.int32),
            cells=wp.array(cells.astype(np.int32), dtype=int, device=self._device),
            colors=wp.zeros(len(cells), dtype=wp.vec3, device=self._device),
        )
        self.speed_slice = speed_slice
        return speed_slice

    def slice_colors(self, speed_slice):
        wp.launch(
            sample_slice,
            dim=len(speed_slice.cells),
            inputs=[speed_slice.colors, speed_slice.cells, self.sim.solver.u_field, self._palette, self._speed_scale],
            device=self._device,
        )
        return speed_slice.colors.numpy()

    def capture(self, steps):
        """Advance the tracers by ``steps`` solver steps and return a new frame."""
        solver = self.sim.solver
        self._frame += 1
        wp.launch(
            advect_tracers,
            dim=self.tracer_count,
            inputs=[
                self._trails,
                self._tracer_colors,
                self._ages,
                solver.u_field,
                solver.flags,
                self._fluid_cells,
                self._seeds,
                self._seed_boundaries,
                solver.boundary_velocity,
                self._palette,
                solver.nx,
                solver.ny,
                solver.nz,
                TRAIL_LENGTH,
                float(steps),
                self._frame,
                self._speed_scale,
                self.sim.jet_speed,
                VENT_SHARE,
                *self._life_steps,
            ],
            device=self._device,
        )

        domain = self.sim.domain
        speed_slice = self.speed_slice
        return FlowFrame(
            tracer_points=(domain.origin + self._trails.numpy() * domain.dx).astype(np.float32),
            tracer_colors=self._tracer_colors.numpy(),
            speed_slice=speed_slice,
            slice_colors=self.slice_colors(speed_slice),
        )
