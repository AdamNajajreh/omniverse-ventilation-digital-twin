import omni.usd
from pxr import UsdGeom, Gf

# ============================================================
# CONFIGURATION
# ============================================================

CEILING_VENT_ANGLE = 45.0
WALL_VENT_ANGLE = 20.0

LOUVER_COUNT = 5

stage = omni.usd.get_context().get_stage()

if stage is None:
    raise RuntimeError("No USD stage is open!")


# ============================================================
# LOUVER CONTROL
# ============================================================

def set_vent_angle(vent_name, angle):
    """
    Rotate all five louvers of a ventilation duct.

    angle = 0   -> closed
    angle = 90  -> open
    """

    angle = max(0.0, min(90.0, float(angle)))

    for i in range(LOUVER_COUNT):

        path = (
            f"/World/Ventilation/"
            f"{vent_name}/LouverPivot_{i}"
        )

        prim = stage.GetPrimAtPath(path)

        if not prim.IsValid():
            print(f"WARNING: Missing {path}")
            continue

        transform = UsdGeom.XformCommonAPI(prim)

        # Rotate around the local X axis
        transform.SetRotate(
            Gf.Vec3f(angle, 0, 0),
            UsdGeom.XformCommonAPI.RotationOrderXYZ
        )

    print(f"{vent_name}: louvers set to {angle} degrees")


# ============================================================
# APPLY SETTINGS
# ============================================================

set_vent_angle("CeilingVent", CEILING_VENT_ANGLE)
set_vent_angle("WallVent", WALL_VENT_ANGLE)

print("Ventilation controls updated!")