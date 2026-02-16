import importlib.util
from pathlib import Path

p = Path(__file__).parent / '02-15-26 Amion elective schedules 3.py'
spec = importlib.util.spec_from_file_location('amion_app', str(p))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

app = mod.app
