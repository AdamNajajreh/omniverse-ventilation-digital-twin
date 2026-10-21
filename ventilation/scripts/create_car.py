import omni.usd
from pxr import Usd, UsdGeom, Gf

# ============================================================
# CONFIGURATION
# ============================================================

CAR_PATH = "/World/CarController"
IMPORTED_CAR_PATH = "/World/concept_1"
FLOOR_PATH = "/World/Room/Floor"

TARGET_LENGTH = 4.3  # meters
CAR_ROTATION_Z = 90.0


# ============================================================
# INITIALIZE
# ============================================================

stage = omni.usd.get_context().get_stage()

if stage is None:
    raise RuntimeError("No USD stage is open!")


def get_bounds(prim):
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_]
    )
    return cache.ComputeWorldBound(prim).ComputeAlignedBox()


# ============================================================
# FIND CAR
# ============================================================

controller_prim = stage.GetPrimAtPath(CAR_PATH)

if not controller_prim.IsValid():
    imported_car = stage.GetPrimAtPath(IMPORTED_CAR_PATH)

    if not imported_car.IsValid():
        raise RuntimeError(
            "Car not found. Import concept 1.glb first."
        )

    # Create controller
    controller_prim = UsdGeom.Xform.Define(
        stage, CAR_PATH
    ).GetPrim()

    # Move imported car under controller
    omni.kit.commands.execute(
        "MovePrim",
        path_from=IMPORTED_CAR_PATH,
        path_to=f"{CAR_PATH}/concept_1"
    )

    controller_prim = stage.GetPrimAtPath(CAR_PATH)

    print("Created CarController.")

else:
    print("CarController already exists.")


# ============================================================
# REMOVE OLD BOX CAR
# ============================================================

old_car = stage.GetPrimAtPath("/World/Car")

if old_car.IsValid():
    stage.RemovePrim("/World/Car")
    print("Removed old box-shaped car.")


# ============================================================
# RESET CONTROLLER TRANSFORMS
# ============================================================

controller = UsdGeom.XformCommonAPI(controller_prim)

controller.SetTranslate(Gf.Vec3d(0, 0, 0))
controller.SetRotate(
    Gf.Vec3f(0, 0, CAR_ROTATION_Z),
    UsdGeom.XformCommonAPI.RotationOrderXYZ
)
controller.SetScale(Gf.Vec3f(1, 1, 1))


# ============================================================
# CALCULATE CORRECT SCALE
# ============================================================

car_box = get_bounds(controller_prim)
size = car_box.GetSize()

# Determine longest horizontal dimension
original_length = max(size[0], size[1])

scale_factor = TARGET_LENGTH / original_length

controller.SetScale(
    Gf.Vec3f(scale_factor, scale_factor, scale_factor)
)

print(f"Car scale: {scale_factor:.3f}")


# ============================================================
# CENTER CAR IN GARAGE
# ============================================================

floor_prim = stage.GetPrimAtPath(FLOOR_PATH)

if not floor_prim.IsValid():
    raise RuntimeError("Garage floor not found!")

car_box = get_bounds(controller_prim)
floor_box = get_bounds(floor_prim)

car_min = car_box.GetMin()
car_max = car_box.GetMax()

floor_min = floor_box.GetMin()
floor_max = floor_box.GetMax()

car_center_x = (car_min[0] + car_max[0]) / 2
car_center_y = (car_min[1] + car_max[1]) / 2

floor_center_x = (floor_min[0] + floor_max[0]) / 2
floor_center_y = (floor_min[1] + floor_max[1]) / 2

dx = floor_center_x - car_center_x
dy = floor_center_y - car_center_y
dz = floor_max[2] - car_min[2]

controller.SetTranslate(Gf.Vec3d(dx, dy, dz))


# ============================================================
# FINAL VERIFICATION
# ============================================================

final_box = get_bounds(controller_prim)

print("\nCar successfully configured!")
print("Final dimensions:", final_box.GetSize())
print("Final position:", controller_prim.GetPath())
print("Ready for ventilation simulation!")