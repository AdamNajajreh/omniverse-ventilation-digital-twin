import omni.usd
from pxr import UsdGeom, Gf, Vt

# ============================================================
# GARAGE CONFIGURATION
# ============================================================

ROOM_WIDTH = 8.0
ROOM_DEPTH = 5.0
ROOM_HEIGHT = 3.5

WALL_THICKNESS = 0.10
FLOOR_THICKNESS = 0.10

ROOM_PATH = "/World/Room"

stage = omni.usd.get_context().get_stage()

if stage is None:
    raise RuntimeError("No USD stage is open!")


# ============================================================
# CREATE / UPDATE ROOM
# ============================================================

UsdGeom.Xform.Define(stage, ROOM_PATH)

# Reset any scaling previously applied to the entire room
room = UsdGeom.Xformable(stage.GetPrimAtPath(ROOM_PATH))
room.ClearXformOpOrder()


def create_box(name, position, size, color):
    path = f"{ROOM_PATH}/{name}"

    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)

    # Replace existing transforms to avoid duplicates
    xform = UsdGeom.Xformable(cube.GetPrim())
    xform.ClearXformOpOrder()

    xform.AddTranslateOp().Set(Gf.Vec3d(*position))
    xform.AddScaleOp().Set(Gf.Vec3f(*size))

    cube.CreateDisplayColorAttr(
        Vt.Vec3fArray([Gf.Vec3f(*color)])
    )


# ============================================================
# FLOOR
# ============================================================

create_box(
    "Floor",
    (0, 0, -FLOOR_THICKNESS / 2),
    (ROOM_WIDTH, ROOM_DEPTH, FLOOR_THICKNESS),
    (0.25, 0.25, 0.28)
)


# ============================================================
# BACK WALL
# ============================================================

create_box(
    "BackWall",
    (0, ROOM_DEPTH / 2, ROOM_HEIGHT / 2),
    (ROOM_WIDTH, WALL_THICKNESS, ROOM_HEIGHT),
    (0.75, 0.75, 0.78)
)


# ============================================================
# LEFT WALL
# ============================================================

create_box(
    "LeftWall",
    (-ROOM_WIDTH / 2, 0, ROOM_HEIGHT / 2),
    (WALL_THICKNESS, ROOM_DEPTH, ROOM_HEIGHT),
    (0.75, 0.75, 0.78)
)


# ============================================================
# RIGHT WALL
# ============================================================

create_box(
    "RightWall",
    (ROOM_WIDTH / 2, 0, ROOM_HEIGHT / 2),
    (WALL_THICKNESS, ROOM_DEPTH, ROOM_HEIGHT),
    (0.75, 0.75, 0.78)
)


# ============================================================
# CEILING
# ============================================================

create_box(
    "Ceiling",
    (0, 0, ROOM_HEIGHT + WALL_THICKNESS / 2),
    (ROOM_WIDTH, ROOM_DEPTH, WALL_THICKNESS),
    (0.85, 0.85, 0.88)
)


print("Garage created successfully!")
print(f"Dimensions: {ROOM_WIDTH} × {ROOM_DEPTH} × {ROOM_HEIGHT} meters")