"""Force all tests onto a throwaway data directory.

models.DATA_DIR is bound at import time from TECHNEWS_DATA_DIR. pytest loads
conftest before any test module, so setting it here guarantees no test can
touch the user's real ~/.technews — later tests delete the history file.
"""

import os
import tempfile

os.environ["TECHNEWS_DATA_DIR"] = tempfile.mkdtemp(prefix="technews-tests-")
