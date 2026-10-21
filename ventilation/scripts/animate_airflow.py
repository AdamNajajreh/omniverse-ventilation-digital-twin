import omni.usd
import omni.kit.app

from pxr import UsdGeom, Gf

# ============================================================
# CONFIGURATION
# ============================================================

PARTICLE_COUNT = 300

CEILING_SPEED = 1.0  # meters per second
WALL_SPEED = 1.5     # meters per second

CEILING_DISTANCE = 2.5
WALL_DISTANCE = 3.5

stage = omni.usd.get_context().get_stage()

# ============================================================
# LOAD PARTICLES
# ============================================================

particles = {
    "CeilingVent": [],
    "WallVent": [],
}

for vent_name in particles:
    for i in range(PARTICLE_COUNT):
        path = f"/World/Airflow/{vent_name}/Particle_{i:03d}"

        prim = stage.GetPrimAtPath(path)

        if not prim.IsValid():
            continue

        transform = UsdGeom.XformCommonAPI(prim)
        position = transform.GetXformVectors(0)[0]

        particles[vent_name].append({
            "transform": transform,
            "position": [
                float(position[0]),
                float(position[1]),
                float(position[2]),
            ],
        })

print("Loaded particles:", {
    name: len(items) for name, items in particles.items()
})

# ============================================================
# ANIMATION
# ============================================================

def update_airflow(event):
    dt = min(event.payload.get("dt", 0.016), 0.05)

    for particle in particles["CeilingVent"]:
        pos = particle["position"]

        pos[2] -= CEILING_SPEED * dt

        if pos[2] < 0.8:
            pos[2] = 3.3

        particle["transform"].SetTranslate(Gf.Vec3d(*pos))

    for particle in particles["WallVent"]:
        pos = particle["position"]

        pos[0] += WALL_SPEED * dt

        if pos[0] > -0.3:
            pos[0] = -3.8

        particle["transform"].SetTranslate(Gf.Vec3d(*pos))


# ============================================================
# START ANIMATION
# ============================================================

# Stop previous animation if script is rerun
if "airflow_subscription" in globals():
    airflow_subscription = None

airflow_subscription = (
    omni.kit.app.get_app()
    .get_update_event_stream()
    .create_subscription_to_pop(
        update_airflow,
        name="VentilationAirflowAnimation"
    )
)

print("Airflow animation started!")