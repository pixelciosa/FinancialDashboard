"""Carga el paquete real `lab` bajo otro nombre (tests/lab lo tapa al importarse como `lab`)."""
import importlib
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def load_lab():
    if 'lab_real' not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            'lab_real', os.path.join(ROOT, 'lab', '__init__.py'),
            submodule_search_locations=[os.path.join(ROOT, 'lab')])
        mod = importlib.util.module_from_spec(spec)
        sys.modules['lab_real'] = mod
        spec.loader.exec_module(mod)
    return {n: importlib.import_module(f'lab_real.{n}') for n in ('api', 'ledger', 'prices', 'engine', 'attribution', 'blocks', 'stress', 'stress_prices', 'journal', 'exposure', 'correlation', 'macro', 'macro_prices', 'refresh')}
