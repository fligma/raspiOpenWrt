"""Run the manager_update test told from the linux side, on this machine.

The test pins the addon's platform answer to ``THE_SYSTEM`` for every section
past the two that ask about systems by name, so re-pointing those four module
constants at linux drives the whole file as a Linux runner would see it: the
ELF program, the plain ``berry_manager`` file name, the ``berry_manager-linux``
release asset.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "test_manager_update.py"

spec = importlib.util.spec_from_file_location("t_mu_linux", TARGET)
module = importlib.util.module_from_spec(spec)
sys.modules["t_mu_linux"] = module
spec.loader.exec_module(module)

module.THE_SYSTEM = "linux"
module.THE_PROGRAM = module.SYSTEM_PROGRAM["linux"]
module.THE_FILE = module.SYSTEM_FILE["linux"]
module.THE_BYTES = module.program_bytes("linux")

raise SystemExit(module.main())
