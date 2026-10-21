import omni.ui as ui
import omni.usd
from pxr import UsdGeom, Gf

# ============================================================
# CONFIGURATION
# ============================================================

LOUVER_COUNT = 5

# ============================================================
# LOUVER CONTROL
# ============================================================

def set_vent_angle(vent_name, angle):
    stage = omni.usd.get_context().get_stage()

    if stage is None:
        return

    angle = max(0.0, min(90.0, float(angle)))

    for i in range(LOUVER_COUNT):
        path = (
            f"/World/Ventilation/"
            f"{vent_name}/LouverPivot_{i}"
        )

        prim = stage.GetPrimAtPath(path)

        if not prim.IsValid():
            continue

        transform = UsdGeom.XformCommonAPI(prim)

        transform.SetRotate(
            Gf.Vec3f(angle, 0, 0),
            UsdGeom.XformCommonAPI.RotationOrderXYZ
        )


# ============================================================
# UI CALLBACKS
# ============================================================

def on_ceiling_changed(model):
    angle = model.get_value_as_float()
    set_vent_angle("CeilingVent", angle)


def on_wall_changed(model):
    angle = model.get_value_as_float()
    set_vent_angle("WallVent", angle)


# ============================================================
# CREATE WINDOW
# ============================================================

fan_speeds = {

    "CeilingVent": 50.0,

    "WallVent": 50.0,

}

def on_ceiling_fan_changed(model):

    fan_speeds["CeilingVent"] = model.get_value_as_float()

    print(f"Ceiling fan: {fan_speeds['CeilingVent']:.0f}%")

def on_wall_fan_changed(model):

    fan_speeds["WallVent"] = model.get_value_as_float()

    print(f"Wall fan: {fan_speeds['WallVent']:.0f}%")

# Close previous window if the script is executed again
if "ventilation_window" in globals():
    ventilation_window.destroy()

ventilation_window = ui.Window(
    "Ventilation Control Panel",
    width=360,
    height=380
)

with ventilation_window.frame:
    with ui.VStack(spacing=15, height=0):

        ui.Label(
            "VENTILATION DIGITAL TWIN",
            height=30,
            style={"font_size": 18}
        )

        ui.Separator()

        # Ceiling vent
        ui.Label("Ceiling Vent Opening (0-90°)")

        ceiling_slider = ui.FloatSlider(
            min=0,
            max=90,
            step=1
        )

        ceiling_slider.model.set_value(45.0)
        ceiling_slider.model.add_value_changed_fn(
            on_ceiling_changed
        )

        # Wall vent
        ui.Label("Wall Vent Opening (0-90°)")

        wall_slider = ui.FloatSlider(
            min=0,
            max=90,
            step=1
        )

        wall_slider.model.set_value(20.0)
        wall_slider.model.add_value_changed_fn(
            on_wall_changed
        )

        # Fan speed controls
        ui.Label("Ceiling Fan Speed (0–100%)")

        ceiling_fan_slider = ui.FloatSlider(
            min=0,
            max=100,
            step=1
        )
        ceiling_fan_slider.model.set_value(50.0)
        ceiling_fan_slider.model.add_value_changed_fn(
            on_ceiling_fan_changed
        )

        ui.Label("Wall Fan Speed (0–100%)")

        wall_fan_slider = ui.FloatSlider(
            min=0,
            max=100,
            step=1
        )
        wall_fan_slider.model.set_value(50.0)
        wall_fan_slider.model.add_value_changed_fn(
            on_wall_fan_changed
        )
        ui.Separator()

        ui.Label(
            "0° = Closed | 90° = Fully Open",
            style={"color": 0xFFAAAAAA}
        )


print("Ventilation Control Panel created!")