# Changelog

## [0.4.0]

### Added
- Unattended visitor mode for the public stream: opens the scene, starts the
  flow, hides the editor, and starts over when a visitor leaves.

## [0.3.0]

### Added
- A slider and live flow readout for every slat, read from the open scene.
- Metrics table with supply, door outflow, air changes, speeds, and still air.
- The panel scrolls and docks beside the Property window.

### Fixed
- Tilted slats created or lost air at the rim of the jet. Inlets now drive only
  the links facing into the room. Door flow matches supply to about 2 %.
- Slats now carry equal flow, regardless of how they fall on the grid.
- Start no longer flattens mixed slat angles to their average.

## [0.2.0]

### Added
- Tracer streaks and a colored speed slice in the viewport, with a legend in the panel.
- Temporary garage light, since the saved scene has no light inside the room.
- Slice plane and position controls, which also work while paused.

### Fixed
- Reset followed by Start could fail, or leave the panel stuck at t = 0. Only the
  solver thread now touches Warp while it runs, and its graph is recorded at build.
- A solver thread failure now shows in the panel.

## [0.1.0]

### Added
- Warp LBM solver with per-slat inlets and an open door outlet.
- Control panel with slat angle and fan sliders for each vent.
