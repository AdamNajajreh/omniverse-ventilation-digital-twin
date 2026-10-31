"""Turn the USD garage into a voxel grid and a list of slatted vents.

The stage is the single source of truth: room size, car position, and vent
placement are all read from prims, so moving them in Kit changes the CFD.
"""

from dataclasses import dataclass, field

import numpy as np
import warp as wp
from pxr import Gf, Usd, UsdGeom

from .solver import FLAG_FLUID, FLAG_INLET, FLAG_OUTLET, FLAG_SOLID

wp.set_module_options({"enable_backward": False})

ROOM_PATH = "/World/Room"
CAR_PATH = "/World/CarController"
VENTILATION_PATH = "/World/Ventilation"

# Half thickness of the voxelized surface, in cells. 1 / sqrt(2) is the smallest
# band that no D3Q19 link can jump across, so air cannot leak through a surface.
SURFACE_HALF_THICKNESS = 0.71

FLOOD_CHECK_INTERVAL = 32


@dataclass
class Vent:
    """A rectangular slatted opening. All vectors are in world space."""

    name: str
    center: np.ndarray  # middle of the opening
    span: np.ndarray  # unit vector along the slat length
    stack: np.ndarray  # unit vector along which the slats are stacked
    normal: np.ndarray  # unit vector pointing into the room
    width: float
    height: float
    slat_paths: list
    slat_angles: list
    first_boundary: int = 0
    slat_cells: list = field(default_factory=list)


@dataclass
class Domain:
    origin: np.ndarray
    dx: float
    flags: np.ndarray
    vents: list
    car_cells: int

    @property
    def fluid_volume(self):
        return float((self.flags == FLAG_FLUID).sum()) * self.dx**3

    @property
    def boundary_count(self):
        return sum(len(vent.slat_angles) for vent in self.vents)


def read_slat_angle(prim):
    return float(UsdGeom.XformCommonAPI(prim).GetXformVectors(Usd.TimeCode.Default())[1][0])


def slat_pivots(vent_prim):
    """Return the pivot prims of a vent in slat order."""
    return sorted(
        (child for child in vent_prim.GetChildren() if child.GetName().startswith("LouverPivot_")),
        key=lambda prim: int(prim.GetName().rsplit("_", 1)[1]),
    )


def _world_box(cache, prim):
    box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
    return np.array(box.GetMin(), dtype=float), np.array(box.GetMax(), dtype=float)


def _triangles(stage, root_path):
    """Return world-space (points, triangle indices) of the visible meshes."""
    xform_cache = UsdGeom.XformCache()
    all_points = []
    all_indices = []
    offset = 0

    for prim in Usd.PrimRange(stage.GetPrimAtPath(root_path)):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        if mesh.ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts:
            continue

        matrix = np.array(xform_cache.GetLocalToWorldTransform(prim), dtype=float)
        local = np.asarray(points, dtype=float)
        world = local @ matrix[:3, :3] + matrix[3, :3]

        indices = np.asarray(indices, dtype=np.int64)
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        for start, count in zip(starts, counts):
            # Triangle fan, so quads and n-gons are covered too.
            for i in range(1, int(count) - 1):
                all_indices.append((indices[start] + offset, indices[start + i] + offset, indices[start + i + 1] + offset))

        all_points.append(world)
        offset += len(world)

    if not all_points:
        return None, None
    return np.concatenate(all_points), np.array(all_indices, dtype=np.int32).ravel()


@wp.kernel
def mark_surface(
    mesh: wp.uint64,
    origin: wp.vec3,
    dx: float,
    nx: int,
    ny: int,
    max_distance: float,
    surface: wp.array(dtype=int),
):
    c = wp.tid()
    k = c // (nx * ny)
    j = (c - k * nx * ny) // nx
    i = c - (k * ny + j) * nx
    point = origin + (wp.vec3(float(i), float(j), float(k)) + wp.vec3(0.5, 0.5, 0.5)) * dx
    query = wp.mesh_query_point_no_sign(mesh, point, max_distance)
    if query.result:
        surface[c] = 1


@wp.kernel
def flood_step(
    solid: wp.array(dtype=int),
    reached_in: wp.array(dtype=int),
    reached_out: wp.array(dtype=int),
    nx: int,
    ny: int,
):
    c = wp.tid()
    if solid[c] != 0:
        return
    stride_z = nx * ny
    near = (
        reached_in[c]
        + reached_in[c - 1]
        + reached_in[c + 1]
        + reached_in[c - nx]
        + reached_in[c + nx]
        + reached_in[c - stride_z]
        + reached_in[c + stride_z]
    )
    reached_out[c] = 1 if near > 0 else 0


def _voxelize_mesh(points, indices, origin, dx, shape, device):
    nx, ny, nz = shape
    surface = wp.zeros(nx * ny * nz, dtype=int, device=device)
    mesh = wp.Mesh(
        points=wp.array(points.astype(np.float32), dtype=wp.vec3, device=device),
        indices=wp.array(indices, dtype=int, device=device),
    )
    wp.launch(
        mark_surface,
        dim=nx * ny * nz,
        inputs=[mesh.id, wp.vec3(*origin), dx, nx, ny, SURFACE_HALF_THICKNESS * dx, surface],
        device=device,
    )
    return surface.numpy().reshape(nz, ny, nx).transpose(2, 1, 0).astype(bool)


def _reachable(solid, seeds, device):
    """Flood fill air from ``seeds``. The boundary layer of ``solid`` must be set."""
    nx, ny, nz = solid.shape
    solid_array = wp.array(solid.ravel(order="F").astype(np.int32), dtype=int, device=device)
    reached_a = wp.array(seeds.ravel(order="F").astype(np.int32), dtype=int, device=device)
    reached_b = wp.zeros(nx * ny * nz, dtype=int, device=device)

    previous = -1
    while True:
        for _ in range(FLOOD_CHECK_INTERVAL):
            wp.launch(flood_step, dim=nx * ny * nz, inputs=[solid_array, reached_a, reached_b, nx, ny], device=device)
            reached_a, reached_b = reached_b, reached_a
        count = int(reached_a.numpy().sum())
        if count == previous:
            break
        previous = count
    return reached_a.numpy().reshape(nz, ny, nx).transpose(2, 1, 0).astype(bool)


def _fill_box(mask, low, high, origin, dx):
    """Set the cells whose centers lie inside the world-space box."""
    first = np.ceil((low - origin) / dx - 0.5).astype(int)
    last = np.ceil((high - origin) / dx - 0.5).astype(int)
    first = np.clip(first, 0, mask.shape)
    last = np.clip(last, 0, mask.shape)
    mask[first[0] : last[0], first[1] : last[1], first[2] : last[2]] = True


def _read_vent(stage, vent_prim):
    duct = stage.GetPrimAtPath(vent_prim.GetPath().AppendChild("Duct"))
    if not duct.IsValid():
        return None

    pivots = slat_pivots(vent_prim)

    # Duct is a unit cube scaled to the vent, with its +z face at the opening.
    matrix = UsdGeom.XformCache().GetLocalToWorldTransform(duct)
    center = matrix.Transform(Gf.Vec3d(0, 0, 0.5))
    span = matrix.TransformDir(Gf.Vec3d(1, 0, 0))
    stack = matrix.TransformDir(Gf.Vec3d(0, 1, 0))
    normal = matrix.TransformDir(Gf.Vec3d(0, 0, 1))

    return Vent(
        name=vent_prim.GetName(),
        center=np.array(center),
        span=np.array(span.GetNormalized()),
        stack=np.array(stack.GetNormalized()),
        normal=np.array(normal.GetNormalized()),
        width=span.GetLength(),
        height=stack.GetLength(),
        slat_paths=[str(prim.GetPath()) for prim in pivots],
        slat_angles=[read_slat_angle(prim) for prim in pivots],
    )


def _mark_inlets(flags, vents, origin, dx):
    """Turn the last layer of duct cells behind each opening into inlet strips."""
    nx, ny, nz = flags.shape
    axes = [origin[a] + (np.arange(flags.shape[a]) + 0.5) * dx for a in range(3)]
    x, y, z = np.meshgrid(axes[0], axes[1], axes[2], indexing="ij", sparse=True)

    boundary = 0
    for vent in vents:
        count = len(vent.slat_angles)
        offset_x, offset_y, offset_z = x - vent.center[0], y - vent.center[1], z - vent.center[2]
        depth = offset_x * vent.normal[0] + offset_y * vent.normal[1] + offset_z * vent.normal[2]
        along = offset_x * vent.span[0] + offset_y * vent.span[1] + offset_z * vent.span[2]
        across = offset_x * vent.stack[0] + offset_y * vent.stack[1] + offset_z * vent.stack[2]

        face = (depth > -dx) & (depth <= 0.0) & (np.abs(along) <= vent.width / 2) & (np.abs(across) <= vent.height / 2)
        strip = np.clip(np.floor((across + vent.height / 2) / (vent.height / count)).astype(int), 0, count - 1)

        vent.first_boundary = boundary
        vent.slat_cells = []
        for slat in range(count):
            cells = face & (strip == slat)
            flags[cells] = FLAG_INLET + boundary + slat
            vent.slat_cells.append(int(cells.sum()))
            if vent.slat_cells[-1] == 0:
                raise RuntimeError(f"{vent.name} slat {slat} is thinner than the grid, reduce the cell size")
        boundary += count


def read_domain(stage, dx, device=None):
    """Voxelize the room, car, and ducts, and locate the vents.

    The -y side of the room has no wall and acts as the open garage door.
    """
    device = wp.get_device(device)
    room = stage.GetPrimAtPath(ROOM_PATH)
    if not room.IsValid():
        raise RuntimeError(f"Scene has no room at {ROOM_PATH}")

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    origin, top = _world_box(cache, room)
    shape = tuple(np.ceil((top - origin) / dx - 1e-6).astype(int).tolist())

    solid = np.zeros(shape, dtype=bool)
    for prim in room.GetChildren():
        _fill_box(solid, *_world_box(cache, prim), origin, dx)

    vents = []
    ventilation = stage.GetPrimAtPath(VENTILATION_PATH)
    if ventilation.IsValid():
        for prim in ventilation.GetChildren():
            vent = _read_vent(stage, prim)
            if vent is not None:
                vents.append(vent)
                _fill_box(solid, *_world_box(cache, stage.GetPrimAtPath(prim.GetPath().AppendChild("Duct"))), origin, dx)

    car_surface = np.zeros(shape, dtype=bool)
    if stage.GetPrimAtPath(CAR_PATH).IsValid():
        points, indices = _triangles(stage, CAR_PATH)
        if points is not None:
            car_surface = _voxelize_mesh(points, indices, origin, dx, shape, device)

    # The door plane is handled separately, so close it for the flood fill.
    enclosed = solid | car_surface
    door = ~enclosed[:, 0, :]
    enclosed[:, 0, :] = True
    enclosed[0, :, :] = enclosed[-1, :, :] = True
    enclosed[:, -1, :] = True
    enclosed[:, :, 0] = enclosed[:, :, -1] = True

    # Air reachable from the door is the fluid. Anything else, like the inside
    # of the car body, becomes solid even if the mesh is not watertight.
    seeds = np.zeros(shape, dtype=bool)
    seeds[:, 1, :] = ~enclosed[:, 1, :]
    fluid = _reachable(enclosed, seeds, device)

    flags = np.full(shape, FLAG_SOLID, dtype=np.int32)
    flags[fluid] = FLAG_FLUID

    door[0, :] = door[-1, :] = False
    door[:, 0] = door[:, -1] = False
    flags[:, 0, :][door] = FLAG_OUTLET

    _mark_inlets(flags, vents, origin, dx)

    car_cells = int((car_surface | (~fluid & ~solid)).sum()) if car_surface.any() else 0
    return Domain(origin=origin, dx=dx, flags=flags, vents=vents, car_cells=car_cells)
