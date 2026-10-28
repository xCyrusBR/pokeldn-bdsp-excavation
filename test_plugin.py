import importlib.util
from pathlib import Path
import zipfile
import ast
import json
from types import SimpleNamespace
import pytest
import trio

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('excavation_service_test', ROOT/'service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def wall(tracker, point):
    tracker.event(0, dict(rec='game_message', data_id=0x19,
                          fields=dict(zoneID=542, MainPos=point)))


def test_gui_introspection_can_read_parser_without_opening_radio():
    parser = service.build_parser()
    args = parser.parse_args([])
    assert args.seconds == 0
    assert args.renew is False
    assert parser.parse_args(['--renew']).renew is True
    assert parser.parse_args(['--seconds', '120', '--check-only']).check_only
    assert service.main(['--check-only']) == 0


def test_new_wall_runs_once_then_rearms_after_a_different_wall_or_close(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(service.time, 'monotonic', lambda: now[0])
    tracker = service.WallTracker()
    wall(tracker, [10, 20])
    now[0] = 12.1
    assert tracker.candidate(now[0]) is None  # no two acknowledged helpers
    tracker.ready.update((0, 1))
    first = tracker.candidate(now[0])
    assert first is not None
    tracker.handled = first
    wall(tracker, [10, 20])
    assert tracker.candidate(now[0]+2.1) is None  # polling cannot rerun a completed wall
    wall(tracker, [11, 20])
    assert tracker.candidate(now[0]) is None  # initial-message grace period
    now[0] += 2.1
    second = tracker.candidate(now[0])
    assert second is not None and second != first
    tracker.handled = second
    tracker.event(0, dict(rec='game_message', data_id=0x28))
    assert tracker.candidate(now[0]) is None
    wall(tracker, [11, 20])
    now[0] += 2.1
    assert tracker.candidate(now[0]) not in (None, second)


def test_secondary_peer_does_not_replace_primary_wall_and_stale_wall_is_not_run(monkeypatch):
    monkeypatch.setattr(service.time, 'monotonic', lambda: 10)
    tracker = service.WallTracker()
    tracker.ready.update((0, 1))
    tracker.event(1, dict(rec='game_message', data_id=0x19, fields=dict(zoneID=1, MainPos=[1, 1])))
    assert tracker.wall is None
    wall(tracker, [10, 20])
    assert tracker.candidate(15) is None


def test_distributable_has_only_the_explicit_public_source_allowlist():
    from build import main
    target = main()
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {'plugin.toml', 'plugin.py', 'service.py', 'README.md',
            'bdsp_connect.py', 'bdsp_dig_pair.py', 'bdsp_dig_pair_sweep.py', 'LICENSE'}
        for name in archive.namelist():
            if name.endswith('.py'): compile(archive.read(name), name, 'exec')


@pytest.mark.parametrize('renew,expected', [(False, 1), (True, 2)])
def test_real_controller_single_wall_or_renewal(renew, expected, tmp_path):
    tree = ast.parse((ROOT/'service.py').read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'controller')
    clock, calls = [10.0], []
    tracker = SimpleNamespace(ready={0, 1}, fatal=None, wall=None, handled=None, generation=0, seen=10)
    tracker.candidate = lambda now: (0, len(calls)) if len(calls) < 2 else None
    async def sleep(seconds): clock[0] += seconds
    async def run_sync(fn): return fn()
    def sweep(*args, **kwargs):
        calls.append(1)
        return {'messages': 1040}
    fake_trio = SimpleNamespace(current_time=lambda: clock[0], sleep=sleep,
                               to_thread=SimpleNamespace(run_sync=run_sync))
    scope = dict(pair=SimpleNamespace(trio=fake_trio), tracker=tracker,
                 sweep=SimpleNamespace(sweep_session=sweep, write=lambda *a: None),
                 args=SimpleNamespace(renew=renew), status=lambda *a: None,
                 time=SimpleNamespace(monotonic=lambda: clock[0]), json=json,
                 partial=__import__('functools').partial)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<controller>', 'exec'), scope)
    trio.run(scope['controller'], tmp_path, 6)
    assert len(calls) == expected

