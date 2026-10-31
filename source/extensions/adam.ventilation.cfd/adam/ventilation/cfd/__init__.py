try:
    from .extension import VentilationCfdExtension
except ImportError:
    # omni.ext only exists inside Kit. The solver also runs from plain Python.
    pass
