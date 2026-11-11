"""Run gearshift straight from a checkout / plugin install, no pip needed.

Hook calls take a short path: no CLI import, and always exit 0.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

if len(sys.argv) > 1 and sys.argv[1] == "hook":
    try:
        from gearshift.claude_code import main as hook_main
        hook_main(sys.argv[2:])
    except Exception:
        pass
    sys.exit(0)

from gearshift.cli import main  # noqa: E402

sys.exit(main())
