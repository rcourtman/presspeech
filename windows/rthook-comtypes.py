"""Keep generated COM wrappers out of the immutable application bundle."""
import sys

# Some collected packages include Python sources alongside their archives.
# Importing those sources must not create bytecode caches in the bundle.
sys.dont_write_bytecode = True

import comtypes.client

# collect_all includes writable Python sources in the one-directory bundle.
# comtypes would otherwise put new type-library wrappers alongside them.
# Its supported memory-only mode also avoids sharing generated modules across
# package versions or different Windows type-library revisions.
comtypes.client.gen_dir = None
