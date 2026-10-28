"""Installation lifecycle for the current operating system."""
import os

if os.name == 'nt':
    from . import migration_windows as implementation
else:
    from . import migration_posix as implementation


def __getattr__(name):
    return getattr(implementation, name)
