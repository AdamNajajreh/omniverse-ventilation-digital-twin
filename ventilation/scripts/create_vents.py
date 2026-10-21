import omni.usd
from pxr import UsdGeom, Gf, Vt

# ============================================================
# CONFIGURATION
# ============================================================

ROOM_WIDTH = 8.0
ROOM_DEPTH = 5.0
ROOM_HEIGHT = 3.5

VENT_WIDTH = 1.2
VENT_HEIGHT = 0.6
DUCT_DEPTH = 0.45

LOUVER_COUNT = 5
LOUVER_THICKNESS = 0.035

VENT_COLOR = (0.35, 0.38, 0.42)
FRAME_COLOR = (0.12, 0.14, 0.17)
LOUVER_COLOR = (0.70, 0.73, 0.76)

stage = omni.usd.get_context().get_stage()

if stage is None:
    raise RuntimeError("No USD stage is open!")

# Remove old ventilation markers and rebuild our new vents
if stage.GetPrimAtPath("/World/Ventilation").IsValid():
    stage.RemovePrim("/World/Ventilation")

UsdGeom.Xform.Define(stage, "/World/Ventilation")


# ============================================================
# GEOMETRY HELPERS
# ============================================================

def create_box(path, position, size, color):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)

    transform = UsdGeom.XformCommonAPI(cube)
    transform.SetTranslate(Gf.Vec3d(*position))
    transform.SetScale(Gf.Vec3f(*size))

    cube.CreateDisplayColorAttr(
        Vt.Vec3fArray([Gf.Vec3f(*color)])
    )

    return cube


def create_vent(name, position, rotation):
    root_path = f"/World/Ventilation/{name}"

    vent = UsdGeom.Xform.Define(stage, root_path)

    transform = UsdGeom.XformCommonAPI(vent)
    transform.SetTranslate(Gf.Vec3d(*position))
    transform.SetRotate(
        Gf.Vec3f(*rotation),
        UsdGeom.XformCommonAPI.RotationOrderXYZ
    )

    # --------------------------------------------------------
    # DUCT BODY
    # --------------------------------------------------------

    create_box(
        f"{root_path}/Duct",
        (0, 0, -DUCT_DEPTH / 2),
        (VENT_WIDTH, VENT_HEIGHT, DUCT_DEPTH),
        VENT_COLOR
    )

    # --------------------------------------------------------
    # FRONT FRAME
    # --------------------------------------------------------

    frame_thickness = 0.06

    create_box(
        f"{root_path}/FrameTop",
        (0, VENT_HEIGHT / 2, 0),
        (VENT_WIDTH, frame_thickness, 0.08),
        FRAME_COLOR
    )

    create_box(
        f"{root_path}/FrameBottom",
        (0, -VENT_HEIGHT / 2, 0),
        (VENT_WIDTH, frame_thickness, 0.08),
        FRAME_COLOR
    )

    create_box(
        f"{root_path}/FrameLeft",
        (-VENT_WIDTH / 2, 0, 0),
        (frame_thickness, VENT_HEIGHT, 0.08),
        FRAME_COLOR
    )

    create_box(
        f"{root_path}/FrameRight",
        (VENT_WIDTH / 2, 0, 0),
        (frame_thickness, VENT_HEIGHT, 0.08),
        FRAME_COLOR
    )

    # --------------------------------------------------------
    # FIVE ROTATING LOUVERS
    # --------------------------------------------------------

    spacing = VENT_HEIGHT / LOUVER_COUNT

    for i in range(LOUVER_COUNT):
        y = (
            -VENT_HEIGHT / 2
            + spacing * (i + 0.5)
        )

        pivot_path = f"{root_path}/LouverPivot_{i}"

        pivot = UsdGeom.Xform.Define(
            stage, pivot_path
        )

        pivot_transform = UsdGeom.XformCommonAPI(pivot)
        pivot_transform.SetTranslate(
            Gf.Vec3d(0, y, 0)
        )

        create_box(
            f"{pivot_path}/Blade",
            (0, 0, 0),
            (
                VENT_WIDTH - 0.10,
                spacing - 0.015,
                LOUVER_THICKNESS
            ),
            LOUVER_COLOR
        )

    print(f"Created {name} with {LOUVER_COUNT} louvers")


# ============================================================
# CEILING VENT
# ============================================================

# Vent faces downward into garage
create_vent(
    name="CeilingVent",
    position=(0, 0.8, ROOM_HEIGHT - 0.10),
    rotation=(180, 0, 0)
)


# ============================================================
# WALL VENT
# ============================================================

# Mounted on left wall, facing into garage
create_vent(
    name="WallVent",
    position=(-ROOM_WIDTH / 2 + 0.10, 0, 2.2),
    rotation=(0, 90, 0)
)


print("\nBoth ventilation ducts created successfully!")