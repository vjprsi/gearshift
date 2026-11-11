import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
TESTS = os.path.join(ROOT, "tests")


def clean_router():
    """A router on the bundled policy only, ignoring any user overrides."""
    from gearshift.policy import load_policy
    from gearshift.router import Router
    return Router(load_policy(use_user_files=False))
