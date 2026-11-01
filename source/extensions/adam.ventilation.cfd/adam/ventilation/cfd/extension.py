import carb.eventdispatcher
import carb.settings
import omni.ext
import omni.ui as ui
import omni.usd

from .controller import SimulationController
from .flow_view import PALETTE, SPEED_RANGE
from .visitor import VisitorMode

WINDOW_TITLE = "Ventilation Control Panel"
VISITOR_SCENE_SETTING = "/exts/adam.ventilation.cfd/visitorScene"
VISITOR_RESET_FILE_SETTING = "/exts/adam.ventilation.cfd/visitorResetFile"
VENT_NAMES = ("CeilingVent", "WallVent")
VENT_LABELS = {"CeilingVent": "Ceiling", "WallVent": "Wall"}
DEFAULT_FAN_PERCENT = 50.0
SLICE_PLANES = (("Side (x-z)", 1), ("Front (y-z)", 0), ("Plan (x-y)", 2))  # label, normal axis
LEGEND_STEPS = 24
SLIDER_LABEL_WIDTH = 60
METRICS = (
    ("Supply", lambda m: f"{m['supply']:.2f} m3/s"),
    ("Door outflow", lambda m: f"{m['door_flow']:.2f} m3/s"),
    ("Air changes", lambda m: f"{m['air_changes']:.0f} per hour"),
    ("Mean speed", lambda m: f"{m['mean_speed']:.2f} m/s"),
    ("Peak speed", lambda m: f"{m['max_speed']:.2f} m/s"),
    ("Still air", lambda m: f"{m['still_air'] * 100:.0f} % of the room"),
)


class VentilationCfdExtension(omni.ext.IExt):
    def on_startup(self, _ext_id):
        self._controller = SimulationController(self._set_status, self._show_metrics, self._on_built)
        self._fan_sliders = {}
        self._angle_sliders = {}
        self._slat_sliders = {}
        self._slat_frames = {}
        self._slat_flows = {}
        self._vent_flows = {}
        self._metric_labels = {}
        self._subscriptions = []
        self._slat_subscriptions = {}
        self._syncing = False  # sliders are being updated from code, not by the user
        self._build_window()

        context = omni.usd.get_context()
        self._stage_opened = carb.eventdispatcher.get_eventdispatcher().observe_event(
            observer_name="adam.ventilation.cfd",
            event_name=context.stage_event_name(omni.usd.StageEventType.OPENED),
            on_event=lambda _event: self._rebuild_slats(),
        )

        settings = carb.settings.get_settings()
        scene = settings.get(VISITOR_SCENE_SETTING)
        self._visitor = None
        if scene:
            reset_file = settings.get(VISITOR_RESET_FILE_SETTING)
            self._visitor = VisitorMode(scene, reset_file, self._controller, self._reset_fans)

    def on_shutdown(self):
        if self._visitor:
            self._visitor.destroy()
        self._stage_opened = None
        self._controller.reset()
        self._controller = None
        self._subscriptions = []
        self._slat_subscriptions = {}
        self._fan_sliders = {}
        self._angle_sliders = {}
        self._slat_sliders = {}
        self._slat_frames = {}
        self._slat_flows = {}
        self._vent_flows = {}
        self._metric_labels = {}
        self._window.destroy()
        self._window = None

    def _build_window(self):
        self._window = ui.Window(WINDOW_TITLE, width=380, height=640)
        self._window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)
        with self._window.frame:
            with ui.ScrollingFrame(horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                with ui.VStack(spacing=8, height=0):
                    ui.Label("VENTILATION DIGITAL TWIN", height=30, style={"font_size": 18})
                    ui.Separator()

                    with ui.HStack(height=30, spacing=6):
                        ui.Button("Start", clicked_fn=self._controller.start)
                        ui.Button("Pause", clicked_fn=self._controller.stop)
                        ui.Button("Reset", clicked_fn=self._controller.reset)

                    self._status = ui.Label("Stopped", height=22, word_wrap=True)
                    self._build_metrics()
                    ui.Separator()

                    for name in VENT_NAMES:
                        self._build_vent_controls(name)
                        ui.Separator()

                    ui.Label("0 = closed, 90 = straight out, 180 = closed", style={"color": 0xFFAAAAAA})
                    ui.Separator()
                    self._build_view_controls()

    def _build_metrics(self):
        for label, _ in METRICS:
            with ui.HStack(height=20):
                ui.Label(label, style={"color": 0xFFAAAAAA})
                self._metric_labels[label] = ui.Label("-", alignment=ui.Alignment.RIGHT)

    def _build_view_controls(self):
        with ui.HStack(height=22):
            self._build_toggle("Tracers", self._controller.set_tracers_visible)
            self._build_toggle("Speed slice", self._controller.set_slice_visible)

        with ui.HStack(height=22, spacing=8):
            ui.Label("Slice plane", width=90)
            self._plane = ui.ComboBox(0, *(label for label, _ in SLICE_PLANES))
        ui.Label("Slice position")
        self._slice_position = ui.FloatSlider(min=0, max=1, step=0.01)

        self._subscriptions.append(self._plane.model.add_item_changed_fn(lambda *_: self._on_slice_changed()))
        self._subscriptions.append(
            self._slice_position.model.subscribe_value_changed_fn(lambda _model: self._on_slice_changed())
        )

        with ui.HStack(height=12):
            for step in range(LEGEND_STEPS):
                position = step / (LEGEND_STEPS - 1) * (len(PALETTE) - 1)
                low = min(int(position), len(PALETTE) - 2)
                mix = position - low
                red, green, blue = (a + (b - a) * mix for a, b in zip(PALETTE[low], PALETTE[low + 1]))
                ui.Rectangle(style={"background_color": ui.color(int(red), int(green), int(blue))})
        with ui.HStack(height=18):
            ui.Label("0 m/s")
            ui.Label(f"{SPEED_RANGE} m/s and above", alignment=ui.Alignment.RIGHT)

    def _build_toggle(self, label, on_changed):
        box = ui.CheckBox(width=22)
        box.model.set_value(True)
        ui.Label(label)
        self._subscriptions.append(box.model.subscribe_value_changed_fn(lambda model: on_changed(model.as_bool)))

    def _slice_axis(self):
        return SLICE_PLANES[self._plane.model.get_item_value_model().as_int][1]

    def _on_slice_changed(self):
        self._controller.set_slice(self._slice_axis(), self._slice_position.model.as_float)

    def _build_vent_controls(self, vent_name):
        with ui.HStack(height=22):
            ui.Label(f"{VENT_LABELS[vent_name]} vent", style={"font_size": 17})
            self._vent_flows[vent_name] = ui.Label("-", alignment=ui.Alignment.RIGHT)

        with ui.HStack(height=22, spacing=8):
            ui.Label("Fan %", width=SLIDER_LABEL_WIDTH)
            fan = ui.FloatSlider(min=0, max=100, step=1)
        fan.model.set_value(DEFAULT_FAN_PERCENT)
        self._fan_sliders[vent_name] = fan
        self._subscriptions.append(
            fan.model.subscribe_value_changed_fn(
                lambda model, vent=vent_name: self._controller.set_fan(vent, model.as_float / 100.0)
            )
        )

        with ui.HStack(height=22, spacing=8):
            ui.Label("All slats", width=SLIDER_LABEL_WIDTH)
            angle = ui.FloatSlider(min=0, max=180, step=1)
        self._angle_sliders[vent_name] = angle
        self._subscriptions.append(
            angle.model.subscribe_value_changed_fn(
                lambda model, vent=vent_name: self._on_vent_angle(vent, model.as_float)
            )
        )

        self._slat_frames[vent_name] = ui.Frame(height=0)
        self._build_slat_sliders(vent_name)

    def _build_slat_sliders(self, vent_name):
        """One row per slat in the open scene, rebuilt when the scene changes.

        Filled directly instead of with a build function, which Kit would delay
        until the frame scrolls into view, leaving the sliders missing until then.
        """
        frame = self._slat_frames[vent_name]
        frame.clear()
        angles = self._controller.slat_angles(vent_name)
        self._slat_sliders[vent_name] = []
        self._slat_flows[vent_name] = []
        self._slat_subscriptions[vent_name] = []
        with frame:
            with ui.VStack(spacing=4, height=0):
                for slat in range(len(angles)):
                    with ui.HStack(height=20, spacing=8):
                        ui.Label(f"Slat {slat + 1}", width=SLIDER_LABEL_WIDTH)
                        slider = ui.FloatSlider(min=0, max=180, step=1)
                        self._slat_flows[vent_name].append(ui.Label("-", width=44, alignment=ui.Alignment.RIGHT))
                    self._slat_sliders[vent_name].append(slider)
                    self._slat_subscriptions[vent_name].append(
                        slider.model.subscribe_value_changed_fn(
                            lambda model, vent=vent_name, index=slat: self._on_slat_angle(vent, index, model.as_float)
                        )
                    )
        self._show_angles(vent_name, angles)

    def _reset_fans(self):
        for fan in self._fan_sliders.values():
            fan.model.set_value(DEFAULT_FAN_PERCENT)

    def _rebuild_slats(self):
        for vent_name in self._slat_frames:
            self._build_slat_sliders(vent_name)

    def _on_vent_angle(self, vent_name, angle):
        if self._syncing:
            return
        self._controller.set_vent_angle(vent_name, angle)
        self._show_angles(vent_name, [angle] * len(self._slat_sliders[vent_name]))

    def _on_slat_angle(self, vent_name, slat, angle):
        if self._syncing:
            return
        self._controller.set_slat_angle(vent_name, slat, angle)
        self._show_angles(vent_name, [slider.model.as_float for slider in self._slat_sliders[vent_name]])

    def _show_angles(self, vent_name, angles):
        """Move the sliders without treating it as a user edit, which would flatten mixed slats."""
        if not angles:
            return
        self._syncing = True
        try:
            self._angle_sliders[vent_name].model.set_value(sum(angles) / len(angles))
            for slider, angle in zip(self._slat_sliders[vent_name], angles):
                slider.model.set_value(angle)
        finally:
            self._syncing = False

    def _set_status(self, text):
        self._status.text = text

    def _show_metrics(self, metrics):
        for label, text in METRICS:
            self._metric_labels[label].text = text(metrics)
        for vent_name, flows in metrics["slat_flow"].items():
            if vent_name in self._vent_flows:
                self._vent_flows[vent_name].text = f"{sum(flows):.2f} m3/s"
            for label, flow in zip(self._slat_flows.get(vent_name, ()), flows):
                label.text = f"{flow:.2f}"

    def _on_built(self, vents):
        """Show the state saved in the scene."""
        for vent in vents:
            if vent.name in self._angle_sliders:
                self._show_angles(vent.name, vent.slat_angles)
        self._slice_position.model.set_value(self._controller.slice_fraction)
