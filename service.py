"""Two independent Pia helpers; automatic, bounded excavation on each new wall."""
import argparse
from datetime import datetime, timezone
from functools import partial
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def status(message, tone='info'):
    print('[excavation]', message, flush=True)
    try:
        from pokeldn import panel
    except ImportError:
        return
    panel.show('excavation-status', 'Automatic excavation', [message], tone=tone)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class WallTracker:
    def __init__(self):
        self.ready = set()
        self.wall = None
        self.seen = 0
        self.changed = 0
        self.handled = None
        self.generation = 0
        self.fatal = None

    def event(self, seat, event):
        kind = event.get('rec')
        if kind == 'ug_join_result' and event.get('reliable_acked'):
            self.ready.add(seat)
        if kind == 'dig_attack_stopped': self.fatal = event.get('reason', 'transport stopped')
        if seat or kind != 'game_message': return
        if event.get('data_id') == 0x19:
            fields = event.get('fields') or {}
            identity = (fields.get('zoneID'), tuple(fields.get('MainPos', ())))
            if identity != self.wall:
                self.wall, self.changed = identity, time.monotonic()
            self.seen = time.monotonic()
        elif event.get('data_id') == 0x28:
            self.close()

    def close(self):
        if self.wall is not None:
            self.wall = None
            self.generation += 1

    def candidate(self, now):
        key = (self.generation, self.wall)
        if (len(self.ready) == 2 and self.wall is not None and key != self.handled
                and now-self.changed >= 2 and now-self.seen < 4):
            return key
        return None


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renew', action='store_true', help='Repeat on new walls until Stop; otherwise clear one wall.')
    parser.add_argument('--seconds', type=int, default=0, help='Optional time limit; 0 runs until Stop.')
    parser.add_argument('--keys')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--check-only', action='store_true')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.seconds != 0 and not 30 <= args.seconds <= 86400:
        raise ValueError('Running time must be 0 (until Stop), or 30 to 86400 seconds')
    if args.check_only:
        print(json.dumps(dict(helpers=2, tool='pickaxe', automatic_new_walls=args.renew,
                              seconds=args.seconds, hits_per_cell=8, overlap=0)))
        return 0
    keys = Path(args.keys).expanduser() if args.keys else next((p for p in (
        Path.home()/'.switch/prod.keys',
        Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'PokeLDN-WonderCard-Manager/prod.keys')
        if p.is_file()), None)
    if keys is None or not keys.is_file(): raise ValueError('prod.keys not found')
    if args.output_dir:
        output = args.output_dir
    else:
        try:
            from pokeldn.plugins import data_folder
            base = Path(data_folder('bdsp-excavation'))
        except ImportError:
            base = HERE.parents[1]/'runs/bdsp-excavation'
        output = base/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    pair_path = HERE/'bdsp_dig_pair.py'
    sweep_path = HERE/'bdsp_dig_pair_sweep.py'
    if not pair_path.exists():
        pair_path = HERE.parents[1]/'tools/bdsp_dig_pair.py'
        sweep_path = HERE.parents[1]/'tools/bdsp_dig_pair_sweep.py'
    pair = load('bdsp_dig_pair', pair_path)
    sweep = load('bdsp_dig_pair_sweep', sweep_path)
    tracker = WallTracker()

    async def controller(folder, seconds):
        status('Two helpers connecting. Open a wall and close its initial message.')
        deadline = pair.trio.current_time()+seconds if seconds else float('inf')
        ready_deadline = pair.trio.current_time()+45
        while pair.trio.current_time() < deadline:
            if len(tracker.ready) < 2 and pair.trio.current_time() > ready_deadline:
                raise RuntimeError('Both Underground helpers did not finish joining within 45 seconds')
            if tracker.fatal:
                status('Stopped: delivery was not confirmed. Restart the tool before continuing.', 'warning')
                raise RuntimeError(tracker.fatal)
            now = time.monotonic()
            if tracker.wall and now-tracker.seen > 5:
                tracker.close()
            key = tracker.candidate(now)
            if key:
                tracker.handled = key
                status('New wall detected. Joining two helpers and clearing with pickaxes.')
                try:
                    result = await pair.trio.to_thread.run_sync(
                        partial(sweep.sweep_session, folder, tool='pickaxe', join=True))
                except (RuntimeError, TimeoutError) as exc:
                    if not args.renew:
                        raise
                    status(f'Wall skipped: {exc}. Waiting for another wall.', 'warning')
                else:
                    result['automatic'] = args.renew
                    result['wall_generation'] = tracker.generation
                    with (folder/'automatic-walls.jsonl').open('a') as out:
                        out.write(json.dumps(result)+'\n')
                    if not args.renew:
                        status('Single sweep delivered. Disconnecting both helpers.')
                        return
                    status('Sweep delivered. Open another wall to repeat automatically.')
                # Polling pauses during a sweep; that deliberate gap is not a wall exit.
                tracker.seen = time.monotonic()
                continue
            if len(tracker.ready) == 2:
                for i in (1, 2):
                    sweep.write(folder/f'helper-{i}', ['0x12:19'])
            await pair.trio.sleep(1.5)

    config = SimpleNamespace(keys=str(keys), output=output, seconds=args.seconds,
                             helpers=2, endpoint_mode='port', event_sink=tracker.event,
                             controller=controller)
    print('Private reports:', output, flush=True)
    try:
        return pair.trio.run(pair.run, config)
    finally:
        status('Disconnected. Automatic excavation stopped.')


if __name__ == '__main__':
    try: sys.exit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
