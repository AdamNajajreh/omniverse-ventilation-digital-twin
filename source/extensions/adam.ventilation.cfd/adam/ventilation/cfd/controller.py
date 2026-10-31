"""Connects the open USD stage, the Kit update loop, and the simulation.

The solver runs on its own thread, paced to real time, so that its speed does
not depend on the RTX frame rate. The main thread only handles UI and USD.

While the solver runs, its thread is the only one that may touch Warp. A device
has a single current stream shared by all threads, and array operations swap it
for a moment, so two threads on the GPU can end up on each other's stream.
"""

import queue
import threading
import time

import carb
import omni.kit.app
import omni.usd
import warp as wp
from pxr import Gf, UsdGeom

from .flow_view import FlowView
from .scene import VENTILATION_PATH, read_domain, read_slat_angle, slat_pivots
from .simulation import CfdConfig, VentilationSimulation
from .visuals import FlowVisuals

STEPS_PER_BATCH = 30  # must be even, the GPU queue is drained between batches
MAX_LAG = 0.25  # seconds of simulated time. Beyond this, stop trying to catch up
METRICS_INTERVAL = 1.0  # seconds of wall time


class SimulationController:
    def __init__(self, on_status, on_metrics, on_built):
        self._on_status = on_status
        self._on_metrics = on_metrics
        self._on_built = on_built
        self._config = CfdConfig()
        self._sim = None
        self._view = None
        self._visuals = None
        self._frame = None  # newest frame from the solver thread, not yet shown
        self._metrics = None  # newest (wall time, metrics) from the solver thread
        self._failure = None  # error that ended the solver thread
        self._gpu_actions = queue.SimpleQueue()
        self._stage_id = None
        self._subscription = None
        self._thread = None
        self._stop_thread = threading.Event()
        self._fans = {}
        self._show_tracers = True
        self._show_slice = True
        self._slice_axis = 1
        self._slice_fraction = None  # None follows the first vent
        self._last_metrics_time = 0.0
        self._last_metrics_sim_time = 0.0

    @property
    def running(self):
        return self._thread is not None

    @property
    def vents(self):
        return self._sim.domain.vents if self._sim else []

    @property
    def slice_fraction(self):
        return self._slice_fraction

    def _build(self):
        context = omni.usd.get_context()
        stage = context.get_stage()
        if stage is None:
            raise RuntimeError("No USD stage is open")

        domain = read_domain(stage, self._config.cell_size)
        self._destroy_visuals()
        self._sim = VentilationSimulation(domain, self._config)
        self._sim.solver.record_batch(STEPS_PER_BATCH)
        self._view = FlowView(self._sim)
        self._visuals = FlowVisuals(stage, domain, self._view.tracer_count)
        self._visuals.set_tracers_visible(self._show_tracers)
        self._visuals.set_slice_visible(self._show_slice)
        self._stage_id = context.get_stage_id()
        for name, fraction in self._fans.items():
            self._sim.set_fan(name, fraction)

        if self._slice_fraction is None:
            self._slice_fraction = self._vent_slice_fraction()
        self._apply_slice()
        self._on_built(domain.vents)
        carb.log_info(f"CFD grid {domain.flags.shape}, {domain.fluid_volume:.1f} m3 of air")

    def _destroy_visuals(self):
        if self._visuals:
            self._visuals.destroy()
        self._visuals = None
        self._frame = None
        self._metrics = None

    def _vent_slice_fraction(self):
        """Return the slice position that passes through the first vent."""
        domain = self._sim.domain
        vents = domain.vents
        if not vents:
            return 0.5
        extent = domain.flags.shape[self._slice_axis] * domain.dx
        return float((vents[0].center[self._slice_axis] - domain.origin[self._slice_axis]) / extent)

    def start(self):
        """Start or resume. A new stage is voxelized first."""
        if self.running:
            return
        try:
            if self._sim is None or self._stage_id != omni.usd.get_context().get_stage_id():
                self._build()
        except RuntimeError as error:
            self._on_status(f"Cannot start: {error}")
            return

        self._last_metrics_time = time.monotonic()
        self._last_metrics_sim_time = self._sim.time
        self._stop_thread.clear()
        self._thread = threading.Thread(target=self._run_solver, args=(self._sim, self._view), daemon=True)
        self._thread.start()
        self._subscription = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._on_update, name="adam.ventilation.cfd"
        )
        self._on_status("Running")

    def stop(self):
        self._subscription = None
        if self._thread:
            self._stop_thread.set()
            self._thread.join()
            self._thread = None
            self._run_gpu_actions()

    def reset(self):
        """Discard the flow and re-read the stage on the next start."""
        self.stop()
        self._destroy_visuals()
        self._sim = None
        self._view = None
        self._on_status("Stopped")

    def set_fan(self, vent_name, fraction):
        self._fans[vent_name] = fraction
        if self._sim:
            self._on_gpu_thread(lambda sim=self._sim: sim.set_fan(vent_name, fraction))
            self._refresh_paused_metrics()

    def _pivots(self, vent_name):
        stage = omni.usd.get_context().get_stage()
        vent = stage.GetPrimAtPath(f"{VENTILATION_PATH}/{vent_name}") if stage else None
        return slat_pivots(vent) if vent else []

    def slat_angles(self, vent_name):
        return [read_slat_angle(pivot) for pivot in self._pivots(vent_name)]

    def _rotate(self, pivot, angle):
        UsdGeom.XformCommonAPI(pivot).SetRotate(Gf.Vec3f(float(angle), 0, 0), UsdGeom.XformCommonAPI.RotationOrderXYZ)

    def set_vent_angle(self, vent_name, angle):
        """Rotate every slat of a vent. Works before the first start too."""
        for pivot in self._pivots(vent_name):
            self._rotate(pivot, angle)
        if self._sim:
            self._on_gpu_thread(lambda sim=self._sim: sim.set_all_slats(vent_name, angle))
            self._refresh_paused_metrics()

    def set_slat_angle(self, vent_name, slat, angle):
        """Rotate one slat. Works before the first start too."""
        self._rotate(self._pivots(vent_name)[slat], angle)
        if self._sim:
            self._on_gpu_thread(lambda sim=self._sim: sim.set_slat(vent_name, slat, angle))
            self._refresh_paused_metrics()

    def _refresh_paused_metrics(self):
        """The solver reports while it runs. When paused, show the new settings now."""
        if not self.running:
            self._on_metrics(self._sim.metrics())

    def set_tracers_visible(self, visible):
        self._show_tracers = visible
        if self._visuals:
            self._visuals.set_tracers_visible(visible)

    def set_slice_visible(self, visible):
        self._show_slice = visible
        if self._visuals:
            self._visuals.set_slice_visible(visible)

    def set_slice(self, axis, fraction):
        """Move the speed slice. Works while paused, to inspect a frozen flow."""
        self._slice_axis = axis
        self._slice_fraction = fraction
        if self._visuals:
            self._apply_slice()

    def _apply_slice(self):
        view = self._view
        axis, fraction = self._slice_axis, self._slice_fraction
        if self.running:
            # The next frame carries the new slice to the viewport. Only the
            # newest request runs, so dragging the slider cannot starve the solver.
            def move_slice():
                if (axis, fraction) == (self._slice_axis, self._slice_fraction):
                    view.set_slice(axis, fraction)

            self._gpu_actions.put(move_slice)
        else:
            speed_slice = view.set_slice(axis, fraction)
            self._visuals.set_slice(speed_slice, view.slice_colors(speed_slice))

    def _on_gpu_thread(self, action):
        if self.running:
            self._gpu_actions.put(action)
        else:
            action()

    def _run_gpu_actions(self):
        while not self._gpu_actions.empty():
            self._gpu_actions.get()()

    def _run_solver(self, sim, view):
        try:
            with wp.ScopedDevice(sim.solver.device):
                next_step_time = time.monotonic()
                next_metrics_time = next_step_time + METRICS_INTERVAL
                while not self._stop_thread.is_set():
                    self._run_gpu_actions()
                    now = time.monotonic()
                    if now < next_step_time:
                        time.sleep(next_step_time - now)
                        continue
                    sim.advance_batch(STEPS_PER_BATCH)
                    self._frame = view.capture(STEPS_PER_BATCH)
                    next_step_time = max(next_step_time + STEPS_PER_BATCH * sim.dt, time.monotonic() - MAX_LAG)
                    if now >= next_metrics_time:
                        self._metrics = (now, sim.metrics())
                        next_metrics_time = now + METRICS_INTERVAL
        except Exception as error:  # the thread would otherwise die with the panel still showing "Running"
            carb.log_error(f"CFD solver thread failed: {error}")
            self._failure = str(error)

    def _on_update(self, _event):
        if omni.usd.get_context().get_stage_id() != self._stage_id:
            self.reset()
            return

        failure, self._failure = self._failure, None
        if failure:
            self.stop()
            self._on_status(f"Stopped: {failure}")
            return

        frame, self._frame = self._frame, None
        if frame:
            self._visuals.show(frame)

        report, self._metrics = self._metrics, None
        if report:
            self._report(*report)

    def _report(self, now, metrics):
        if not metrics["stable"]:
            self.stop()
            self._on_status("Stopped: the flow diverged, press Reset")
            return

        rate = (metrics["time"] - self._last_metrics_sim_time) / (now - self._last_metrics_time)
        self._last_metrics_time = now
        self._last_metrics_sim_time = metrics["time"]
        self._on_status(f"t = {metrics['time']:.1f} s   ({rate:.2f}x real time)")
        self._on_metrics(metrics)
