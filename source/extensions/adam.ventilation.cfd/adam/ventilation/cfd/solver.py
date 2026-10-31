"""D3Q19 lattice Boltzmann solver running on the GPU through Warp.

Collision is regularized BGK with a Smagorinsky LES closure, which keeps the
scheme stable at the very low lattice viscosity of air on a 5 cm grid.

Distributions are stored as ``g = f - w`` (density deviation), so that small
velocities are not lost to float32 round-off next to the unit background
density. The grid is flattened as ``i + nx * (j + ny * k)``.

The cell flag array also carries the boundary conditions:
    FLAG_FLUID   regular fluid cell
    FLAG_SOLID   no-slip wall (halfway bounce-back)
    FLAG_OUTLET  open boundary at the -y face, held at reference density
    >= FLAG_INLET moving wall whose velocity is
                  ``boundary_velocity[flag - FLAG_INLET]``. Only links along its
                  ``boundary_normal`` are driven. An inlet can touch air at its
                  sides too, and driving those links with a tilted velocity
                  would create or destroy mass at the rim of the jet.
"""

import numpy as np
import warp as wp

wp.set_module_options({"enable_backward": False, "max_unroll": 32})

FLAG_FLUID = 0
FLAG_SOLID = 1
FLAG_OUTLET = 2
FLAG_INLET = 3

Q = 19

# Extra relaxation time that fades out over the rows next to the door, to absorb
# ripples and vortices before they reach the open boundary.
SPONGE_ROWS = 12
SPONGE_TAU = 0.15

# fmt: off
EX = wp.constant(wp.types.vector(Q, int)(0, 1, -1, 0,  0, 0,  0, 1, -1,  1, -1, 1, -1,  1, -1, 0,  0,  0,  0))
EY = wp.constant(wp.types.vector(Q, int)(0, 0,  0, 1, -1, 0,  0, 1, -1, -1,  1, 0,  0,  0,  0, 1, -1,  1, -1))
EZ = wp.constant(wp.types.vector(Q, int)(0, 0,  0, 0,  0, 1, -1, 0,  0,  0,  0, 1, -1, -1,  1, 1, -1, -1,  1))
OPP = wp.constant(wp.types.vector(Q, int)(0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17))
WEIGHTS = (1.0 / 3.0,) + (1.0 / 18.0,) * 6 + (1.0 / 36.0,) * 12
W = wp.constant(wp.types.vector(Q, float)(*WEIGHTS))
# fmt: on

CS2 = wp.constant(1.0 / 3.0)

VecQ = wp.types.vector(Q, float)


@wp.kernel
def stream_collide(
    g_in: wp.array(dtype=float),
    g_out: wp.array(dtype=float),
    u_field: wp.array(dtype=wp.vec3),
    flags: wp.array(dtype=int),
    boundary_velocity: wp.array(dtype=wp.vec3),
    boundary_normal: wp.array(dtype=wp.vec3),
    nx: int,
    ny: int,
    nz: int,
    tau0: float,
    smagorinsky: float,
):
    c = wp.tid()
    flag = flags[c]
    if flag == FLAG_SOLID or flag >= FLAG_INLET:
        return

    n = nx * ny * nz
    stride_z = nx * ny

    if flag == FLAG_OUTLET:
        # Equilibrium at reference density, velocity taken from the first
        # fluid cell inside the domain. Air may leave or re-enter the door.
        u = u_field[c + nx]
        usq = wp.dot(u, u)
        for q in range(Q):
            cu = float(EX[q]) * u[0] + float(EY[q]) * u[1] + float(EZ[q]) * u[2]
            g_out[c + q * n] = W[q] * (3.0 * cu + 4.5 * cu * cu - 1.5 * usq)
        return

    # Pull streaming with halfway bounce-back at walls and inlets.
    # Read constants before branching: Warp merges a global first referenced
    # inside a branch, and the merged copy is unset when the branch is skipped.
    g = VecQ()
    for q in range(Q):
        ex = float(EX[q])
        ey = float(EY[q])
        ez = float(EZ[q])
        weight = W[q]
        reflected = g_in[c + OPP[q] * n]
        neighbor = c - EX[q] - EY[q] * nx - EZ[q] * stride_z
        neighbor_flag = flags[neighbor]
        if neighbor_flag == FLAG_FLUID or neighbor_flag == FLAG_OUTLET:
            g[q] = g_in[neighbor + q * n]
        elif neighbor_flag == FLAG_SOLID:
            g[q] = reflected
        else:
            wall = boundary_velocity[neighbor_flag - FLAG_INLET]
            facing = boundary_normal[neighbor_flag - FLAG_INLET]
            g[q] = reflected
            if ex * facing[0] + ey * facing[1] + ez * facing[2] > 0.0:
                g[q] = reflected + 6.0 * weight * (ex * wall[0] + ey * wall[1] + ez * wall[2])

    # Moments. The second moment of g is the stress minus cs2 * identity.
    rho_dev = 0.0
    mx = 0.0
    my = 0.0
    mz = 0.0
    pxx = 0.0
    pyy = 0.0
    pzz = 0.0
    pxy = 0.0
    pxz = 0.0
    pyz = 0.0
    for q in range(Q):
        ex = float(EX[q])
        ey = float(EY[q])
        ez = float(EZ[q])
        v = g[q]
        rho_dev += v
        mx += ex * v
        my += ey * v
        mz += ez * v
        pxx += ex * ex * v
        pyy += ey * ey * v
        pzz += ez * ez * v
        pxy += ex * ey * v
        pxz += ex * ez * v
        pyz += ey * ez * v

    rho = 1.0 + rho_dev
    inv_rho = 1.0 / rho
    ux = mx * inv_rho
    uy = my * inv_rho
    uz = mz * inv_rho
    usq = ux * ux + uy * uy + uz * uz
    u_field[c] = wp.vec3(ux, uy, uz)

    # Non-equilibrium stress.
    pxx -= rho_dev * CS2 + rho * ux * ux
    pyy -= rho_dev * CS2 + rho * uy * uy
    pzz -= rho_dev * CS2 + rho * uz * uz
    pxy -= rho * ux * uy
    pxz -= rho * ux * uz
    pyz -= rho * uy * uz

    # Smagorinsky: solve the eddy viscosity implicitly from the local stress.
    stress = wp.sqrt(2.0 * (pxx * pxx + pyy * pyy + pzz * pzz + 2.0 * (pxy * pxy + pxz * pxz + pyz * pyz)))
    tau = 0.5 * (tau0 + wp.sqrt(tau0 * tau0 + 18.0 * smagorinsky * smagorinsky * stress * inv_rho))
    j = (c % stride_z) // nx
    if j < SPONGE_ROWS:
        fade = 1.0 - float(j) / float(SPONGE_ROWS)
        tau += SPONGE_TAU * fade * fade
    keep = 1.0 - 1.0 / tau

    trace = pxx + pyy + pzz
    for q in range(Q):
        ex = float(EX[q])
        ey = float(EY[q])
        ez = float(EZ[q])
        cu = ex * ux + ey * uy + ez * uz
        equilibrium = W[q] * (rho_dev + rho * (3.0 * cu + 4.5 * cu * cu - 1.5 * usq))
        # Regularization: keep only the second-order non-equilibrium part.
        contraction = (
            ex * ex * pxx
            + ey * ey * pyy
            + ez * ez * pzz
            + 2.0 * (ex * ey * pxy + ex * ez * pxz + ey * ez * pyz)
            - CS2 * trace
        )
        g_out[c + q * n] = equilibrium + keep * 4.5 * W[q] * contraction


class LbmSolver:
    """Owns the GPU state of one simulation. All units here are lattice units."""

    def __init__(self, flags, boundary_count, tau0, smagorinsky, device=None):
        """``flags`` is an int array of shape (nx, ny, nz)."""
        self.nx, self.ny, self.nz = flags.shape
        self.tau0 = float(tau0)
        self.smagorinsky = float(smagorinsky)
        self.device = wp.get_device(device)
        self.steps = 0

        cells = self.nx * self.ny * self.nz
        self.flags = wp.array(flags.ravel(order="F").astype(np.int32), dtype=int, device=self.device)
        self.g_a = wp.zeros(Q * cells, dtype=float, device=self.device)
        self.g_b = wp.zeros(Q * cells, dtype=float, device=self.device)
        self.u_field = wp.zeros(cells, dtype=wp.vec3, device=self.device)
        self.boundary_velocity = wp.zeros(max(boundary_count, 1), dtype=wp.vec3, device=self.device)
        self.boundary_normal = wp.zeros(max(boundary_count, 1), dtype=wp.vec3, device=self.device)
        self._batch = None
        self._batch_steps = 0

    def reset(self):
        self.g_a.zero_()
        self.g_b.zero_()
        self.u_field.zero_()
        self.steps = 0

    def set_boundary_velocity(self, velocities):
        """``velocities`` is an (m, 3) array, indexed like the inlet flags."""
        self.boundary_velocity.assign(np.asarray(velocities, dtype=np.float32))

    def set_boundary_normal(self, normals):
        """``normals`` is an (m, 3) array of unit vectors pointing into the fluid."""
        self.boundary_normal.assign(np.asarray(normals, dtype=np.float32))

    def _launch_steps(self, count):
        for _ in range(count):
            wp.launch(
                stream_collide,
                dim=self.nx * self.ny * self.nz,
                inputs=[
                    self.g_a,
                    self.g_b,
                    self.u_field,
                    self.flags,
                    self.boundary_velocity,
                    self.boundary_normal,
                    self.nx,
                    self.ny,
                    self.nz,
                    self.tau0,
                    self.smagorinsky,
                ],
                device=self.device,
            )
            self.g_a, self.g_b = self.g_b, self.g_a

    def step(self, count=1):
        self._launch_steps(count)
        self.steps += count

    def record_batch(self, count):
        """Record an even number of steps as one CUDA graph.

        A single launch call avoids contending for the Python GIL once per
        step when the solver shares the process with Kit's main thread.
        Recording also loads the kernels, so do it before any other thread
        starts using Warp: a capture does not survive other work interfering.
        """
        wp.capture_begin(self.device, force_module_load=True)
        try:
            self._launch_steps(count)
        finally:
            self._batch = wp.capture_end(self.device)
        self._batch_steps = count

    def step_batch(self, count):
        if self._batch_steps != count:
            self.record_batch(count)
        wp.capture_launch(self._batch)
        self.steps += count

    def velocity(self):
        """Return the velocity field as an (nx, ny, nz, 3) numpy array."""
        wp.synchronize_device(self.device)
        return self.u_field.numpy().reshape(self.nz, self.ny, self.nx, 3).transpose(2, 1, 0, 3)
