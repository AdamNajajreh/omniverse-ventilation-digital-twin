"""Unattended mode for the public stream.

Visitors get the viewport and the control panel and nothing else, so the editor
cannot be used to reach the server's files. Each one finds the simulation running
from the saved scene. The web gateway touches a file when a visitor leaves.
"""

import asyncio
import os

import carb
import carb.settings
import carb.tokens
import omni.kit.async_engine
import omni.kit.mainwindow
import omni.ui as ui
import omni.usd

RENDERER_START_SECONDS = 25.0  # opening a stage during RTX startup can crash Kit
RESET_POLL_SECONDS = 1.0
RENDER_MODE = "RaytracedLighting"  # path tracing is too slow to share the GPU with the solver and encoder
EDITOR_WINDOWS = ("Stage", "Layer", "Render Settings", "Content", "Console", "Property", "Main ToolBar")


class VisitorMode:
    def __init__(self, scene, reset_file, controller, reset_controls):
        tokens = carb.tokens.get_tokens_interface()
        self._scene = os.path.normpath(tokens.resolve(scene))
        self._reset_file = reset_file
        self._controller = controller
        self._reset_controls = reset_controls
        self._task = omni.kit.async_engine.run_coroutine(self._run())

    def destroy(self):
        self._task.cancel()

    def _reset_stamp(self):
        try:
            return os.stat(self._reset_file).st_mtime_ns
        except (OSError, TypeError):
            return None

    async def _run(self):
        await asyncio.sleep(RENDERER_START_SECONDS)
        stamp = self._reset_stamp()
        await self._begin()
        while True:
            await asyncio.sleep(RESET_POLL_SECONDS)
            latest = self._reset_stamp()
            if latest != stamp:
                stamp = latest
                await self._begin()

    async def _begin(self):
        """Discard the last visitor's changes and run the saved scene again."""
        self._controller.reset()
        self._reset_controls()
        opened, error = await omni.usd.get_context().open_stage_async(self._scene)
        if not opened:
            carb.log_error(f"Cannot open {self._scene}: {error}")
            return
        carb.settings.get_settings().set("/rtx/rendermode", RENDER_MODE)
        self._hide_editor()
        self._controller.start()

    def _hide_editor(self):
        window = omni.kit.mainwindow.get_main_window()
        window.get_main_menu_bar().visible = False
        window.get_status_bar_frame().visible = False
        for title in EDITOR_WINDOWS:
            ui.Workspace.show_window(title, False)
