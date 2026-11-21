"""Fase 0 del Portfolio Lab: andamiaje, activación por variable y contrato de tokens."""
import json
import os
import re
import subprocess
import sys
import textwrap

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SCRIPT = textwrap.dedent('''
    import json, sys
    if sys.argv[1] == "broken":
        sys.modules["lab"] = None
        sys.modules["lab.api"] = None
    import server
    c = server.app.test_client()
    out = {}
    for path in ("/api/lab/health", "/portfolio", "/api/lab/nada", "/"):
        out[path] = c.get(path).status_code
    h = c.get("/api/lab/health")
    out["health_json"] = h.get_json(silent=True)
    print("RESULT" + json.dumps(out))
''')


def _run(lab_flag, mode='normal'):
    env = {k: v for k, v in os.environ.items() if k != 'PORTFOLIO_LAB'}
    env['NVIDIA_API_KEY'] = ''
    if lab_flag:
        env['PORTFOLIO_LAB'] = '1'
    r = subprocess.run([sys.executable, '-c', _SCRIPT, mode], cwd=PROJECT_DIR, env=env,
                       capture_output=True, text=True, timeout=60)
    line = [l for l in r.stdout.splitlines() if l.startswith('RESULT')]
    assert line, r.stdout + r.stderr
    return json.loads(line[-1][6:])


def test_enabled_routes_respond():
    out = _run(True)
    assert out['/api/lab/health'] == 200
    h = out['health_json']
    assert h['ok'] is True and h['phase'] == 0                     # contrato original intacto
    assert set(h['caches']) == {'precios', 'estres', 'macro'} and 'ultimo_refresh' in h
    assert out['/portfolio'] == 200
    assert out['/api/lab/nada'] == 404


def test_disabled_routes_404():
    out = _run(False)
    assert out['/api/lab/health'] == 404
    assert out['/portfolio'] == 404


def test_broken_import_does_not_break_app():
    out = _run(True, 'broken')
    assert out['/api/lab/health'] == 404
    assert out['/portfolio'] == 404
    assert out['/'] == 200


def test_lab_config_has_excluded_platforms_and_threshold():
    with open(os.path.join(PROJECT_DIR, 'config', 'lab', 'config.json')) as f:
        cfg = json.load(f)
    assert isinstance(cfg['excluded_platforms'], list) and 'umbral_recorte_pct' in cfg


def _tokens(css_block):
    return dict(re.findall(r'(--[a-z0-9]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;', css_block))


def _root_block(path):
    with open(path, encoding='utf-8') as f:
        text = f.read(6000)  # el :root está al inicio; no hace falta leer el archivo entero
    m = re.search(r':root\s*\{(.*?)\}', text, re.S)
    assert m, f'sin :root en {path}'
    return m.group(1)


def test_tokens_match_dashboard():
    lab = _tokens(_root_block(os.path.join(PROJECT_DIR, 'lab_ui', 'tokens.css')))
    main = _tokens(_root_block(os.path.join(PROJECT_DIR, 'index.html')))
    assert len(lab) >= 12
    for k, v in lab.items():
        assert main.get(k, '').lower() == v.lower(), f'{k}: lab={v} index={main.get(k)}'
