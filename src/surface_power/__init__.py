"""linux-surface-power: power and thermal management for Microsoft Surface devices, driven by the device's own SMF blob.

Package layout (see docs/requirements/architecture.md):
- loader:  get the blob, decode it, preprocess and parse it (docs/specs/blob-format.md)
- engine:  build the block graph and evaluate it like the Windows SMF core (docs/specs/engine.md, controller.md)
- tools:   command-line tools (docs/specs/tools.md)
"""

__version__ = "0.1.0"
