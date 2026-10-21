import omni.usd
from pxr import UsdGeom, Gf, Vt
import math
import random

# ============================================================
# CONFIGURATION
# ============================================================

PARTICLES_PER_VENT = 300
PARTICLE_RADIUS = 0.018

ROOM_WIDTH = 8.0
ROOM_HEIGHT = 3.5

stage = omni.usd.get_context().get_stage()

if stage is None:
    raise RuntimeError("No USD stage is open!")

# Recreate airflow visualization
if stage.GetPrimAtPath("/World/Airflow").IsValid():
    stage.RemovePrim("/World/Airflow")

UsdGeom.Xform.Define(stage, "/World/Airflow")


# ============================================================
# PARTICLE CREATION
# ============================================================

def create_particle(path, position, color):
    sphere = UsdGeom.Sphere.Define(stage, path)
    sphere.CreateRadiusAttr(PARTICLE_RADIUS)

    transform = UsdGeom.XformCommonAPI(sphere)
    transform.SetTranslate(Gf.Vec3d(*position))

    sphere.CreateDisplayColorAttr(
        Vt.Vec3fArray([Gf.Vec3f(*color)])
    )

    return sphere


def create_airflow(vent_name, origin, direction, color):
    root_path = f"/World/Airflow/{vent_name}"

    UsdGeom.Xform.Define(stage, root_path)

    random.seed(42)

    for i in range(PARTICLES_PER_VENT):

        # Random distance along airflow direction
        progress = random.random()

        # Random spread across the vent opening
        spread_x = random.uniform(-0.5, 0.5)
        spread_y = random.uniform(-0.25, 0.25)

        if vent_name == "CeilingVent":
            x = origin[0] + spread_x
            y = origin[1] + spread_y
            z = origin[2] - progress * 2.5

        else:
            x = origin[0] + progress * 3.5
            y = origin[1] + spread_x
            z = origin[2] + spread_y

        create_particle(
            f"{root_path}/Particle_{i:03d}",
            (x, y, z),
            color
        )

    print(f"Created {PARTICLES_PER_VENT} particles for {vent_name}")


# ============================================================
# CEILING AIRFLOW
# ============================================================

create_airflow(
    vent_name="CeilingVent",
    origin=(0, 0.8, 3.3),
    direction=(0, 0, -1),
    color=(0.0, 0.8, 1.0)
)


# ============================================================
# WALL AIRFLOW
# ============================================================

create_airflow(
    vent_name="WallVent",
    origin=(-3.8, 0, 2.2),
    direction=(1, 0, 0),
    color=(1.0, 0.55, 0.1)
)


print("\nAirflow particles created successfully!")