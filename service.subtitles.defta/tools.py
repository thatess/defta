import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from defta.tools import main  # noqa: E402

main(sys.argv)
