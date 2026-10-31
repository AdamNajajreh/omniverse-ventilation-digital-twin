"""Show a FlowFrame in the viewport as ordinary USD prims.

RTX draws them with correct occlusion against the car. They live in the session
layer, so the airflow never ends up in a saved scene.
"""

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt

from .flow_view import TRAIL_LENGTH

VISUALS_PATH = "/World/CfdVisuals"
TRACER_WIDTH = (0.022, 0.004)  # m, from head to tail
GLOW = 0.6  # emissive multiplier, tuned so the viewport matches the panel legend
LIGHT_INTENSITY = 1500.0
LIGHT_DROP = 0.2  # m below the top of the room


def _vec3f(values):
    return Vt.Vec3fArray.FromNumpy(np.asarray(values, dtype=np.float32))


class FlowVisuals:
    def __init__(self, stage, domain, tracer_count):
        self._stage = stage
        self._layer = stage.GetSessionLayer()
        self.speed_slice = None

        low = domain.origin
        high = low + np.array(domain.flags.shape) * domain.dx
        extent = Vt.Vec3fArray([Gf.Vec3f(*low), Gf.Vec3f(*high)])

        with Usd.EditContext(stage, self._layer):
            UsdGeom.Scope.Define(stage, VISUALS_PATH)
            material = self._define_material()
            self._tracers = self._define_tracers(tracer_count, extent, material)
            self._slice = self._define_slice(extent, material)
            self._define_light(low, high)

    def _define_material(self):
        """Unlit, with the color coming from primvars:displayColor."""
        stage = self._stage
        material = UsdShade.Material.Define(stage, f"{VISUALS_PATH}/FlowColor")
        reader = UsdShade.Shader.Define(stage, f"{VISUALS_PATH}/FlowColor/ReadColor")
        reader.CreateIdAttr("UsdPrimvarReader_float3")
        reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
        reader.CreateInput("fallback", Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(1.0, 0.0, 1.0))
        color = reader.CreateOutput("result", Sdf.ValueTypeNames.Float3)

        surface = UsdShade.Shader.Define(stage, f"{VISUALS_PATH}/FlowColor/Surface")
        surface.CreateIdAttr("UsdPreviewSurface")
        surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))
        surface.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)
        surface.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(color)
        material.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput("surface", Sdf.ValueTypeNames.Token))
        return material

    def _style(self, gprim, extent, material):
        gprim.CreateExtentAttr(extent)
        # A measurement overlay must not light the room or show up in reflections.
        gprim.GetPrim().CreateAttribute("primvars:doNotCastShadows", Sdf.ValueTypeNames.Bool).Set(True)
        gprim.GetPrim().CreateAttribute("primvars:invisibleToSecondaryRays", Sdf.ValueTypeNames.Bool).Set(True)
        UsdShade.MaterialBindingAPI.Apply(gprim.GetPrim()).Bind(material)
        return gprim

    def _define_tracers(self, count, extent, material):
        curves = UsdGeom.BasisCurves.Define(self._stage, f"{VISUALS_PATH}/Tracers")
        curves.CreateTypeAttr(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr(Vt.IntArray([TRAIL_LENGTH] * count))
        curves.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
        widths = np.tile(np.linspace(*TRACER_WIDTH, TRAIL_LENGTH), count)
        curves.CreateWidthsAttr(Vt.FloatArray.FromNumpy(widths.astype(np.float32)))
        curves.CreatePointsAttr(_vec3f(np.zeros((count * TRAIL_LENGTH, 3))))
        curves.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(_vec3f(np.zeros((count * TRAIL_LENGTH, 3))))
        return self._style(curves, extent, material)

    def _define_slice(self, extent, material):
        mesh = UsdGeom.Mesh.Define(self._stage, f"{VISUALS_PATH}/SpeedSlice")
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)
        return self._style(mesh, extent, material)

    def _define_light(self, low, high):
        """Light the garage, since a distant light cannot reach inside it."""
        light = UsdLux.RectLight.Define(self._stage, f"{VISUALS_PATH}/GarageLight")
        light.CreateWidthAttr(float(high[0] - low[0]) * 0.6)
        light.CreateHeightAttr(float(high[1] - low[1]) * 0.6)
        light.CreateIntensityAttr(LIGHT_INTENSITY)
        light.GetPrim().CreateAttribute("visibleInPrimaryRay", Sdf.ValueTypeNames.Bool).Set(False)
        center = (low + high) / 2
        UsdGeom.XformCommonAPI(light).SetTranslate(Gf.Vec3d(center[0], center[1], high[2] - LIGHT_DROP))

    def set_tracers_visible(self, visible):
        self._set_visible(self._tracers, visible)

    def set_slice_visible(self, visible):
        self._set_visible(self._slice, visible)

    def _set_visible(self, gprim, visible):
        with Usd.EditContext(self._stage, self._layer):
            gprim.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)

    def set_slice(self, speed_slice, colors):
        with Usd.EditContext(self._stage, self._layer), Sdf.ChangeBlock():
            self._slice.GetPointsAttr().Set(_vec3f(speed_slice.points))
            self._slice.GetFaceVertexCountsAttr().Set(Vt.IntArray([4] * (len(speed_slice.face_indices) // 4)))
            self._slice.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(speed_slice.face_indices))
            self._slice.GetDisplayColorPrimvar().Set(_vec3f(colors * GLOW))
        self.speed_slice = speed_slice

    def show(self, frame):
        with Usd.EditContext(self._stage, self._layer):
            self._tracers.GetPointsAttr().Set(_vec3f(frame.tracer_points))
            self._tracers.GetDisplayColorPrimvar().Set(_vec3f(frame.tracer_colors * GLOW))
            if frame.speed_slice is self.speed_slice:
                self._slice.GetDisplayColorPrimvar().Set(_vec3f(frame.slice_colors * GLOW))
        if frame.speed_slice is not self.speed_slice:
            self.set_slice(frame.speed_slice, frame.slice_colors)

    def destroy(self):
        with Usd.EditContext(self._stage, self._layer):
            self._stage.RemovePrim(VISUALS_PATH)
