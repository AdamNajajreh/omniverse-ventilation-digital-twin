# Ventilation CFD

Real-time airflow in the garage scene, solved on the GPU with a D3Q19 lattice
Boltzmann method in Warp.

- `solver.py`: the LBM kernel, in lattice units.
- `scene.py`: voxelizes the room, car, and ducts from USD and locates the vents.
- `simulation.py`: physical units, fans, slats, and flow metrics.
- `flow_view.py`: tracers and the speed slice, computed on the GPU.
- `visuals.py`: shows them as USD prims in the session layer.
- `controller.py` and `extension.py`: the Kit update loop and control panel.

`ventilation/validation` holds the lid-driven cavity benchmark, a headless
garage run, and checks of the slats and the flow visuals. All work outside Kit.
