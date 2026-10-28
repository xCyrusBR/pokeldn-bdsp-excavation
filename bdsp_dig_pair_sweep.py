"""Bounded coordinated sweep after two real excavation permissions; no reconnects."""
import argparse
import json
import struct
import time
from pathlib import Path
try:
    from bdsp_dig_pair import partition_cells
except ImportError:
    from tools.bdsp_dig_pair import partition_cells


def read(path):
    rows = []
    for line in path.read_text().splitlines():
        try: rows.append(json.loads(line))
        except json.JSONDecodeError: pass
    return rows


def attack(cell, hammer=False):
    x, y = cell
    return '0x27:' + struct.pack('<ffI', (x+.5)*60, -(y+.5)*60, int(hammer)).hex()


def write(folder, lines):
    with (folder/'inject.txt').open('a') as out:
        out.write('\n'.join(lines)+'\n')


def join_current_wall(folders):
    """Refresh wall coordinates, then stop resending as soon as a reply arrives."""
    initial = [read(p/'capture.jsonl') for p in folders]
    wall_counts = [sum(r.get('rec') == 'game_message' and r.get('data_id') == 0x19
                       for r in rows) for rows in initial]
    for folder in folders: write(folder, ['0x12:19', '0x12:19'])
    deadline = time.monotonic()+10
    while True:
        walls = [[r for r in read(p/'capture.jsonl')
                  if r.get('rec') == 'game_message' and r.get('data_id') == 0x19] for p in folders]
        if all(len(w) >= count+2 for w, count in zip(walls, wall_counts)): break
        if time.monotonic() > deadline: raise TimeoutError('Current wall was not received')
        time.sleep(.05)
    identities = [(w[-1]['fields']['zoneID'], w[-1]['fields']['MainPos']) for w in walls]
    if identities[0] != identities[1]: raise RuntimeError('Helpers received different walls')
    pending = set(range(2))
    since = [max(r.get('t', 0) for r in read(p/'capture.jsonl')) for p in folders]
    commands = []
    for i, (folder, wall) in enumerate(zip(folders, walls)):
        fields = wall[-1]['fields']
        x, y = fields[f'digPos{i+1}.GridX'], fields[f'digPos{i+1}.GridY']
        if x < 0 or y < 0: raise RuntimeError('Helper position is unavailable')
        commands.append('0x26:'+struct.pack('<ii', x, y).hex())
        write(folder, [commands[-1]])
    for attempt in range(3):
        deadline = time.monotonic()+3
        while pending and time.monotonic() < deadline:
            for i in list(pending):
                replies = [r for r in read(folders[i]/'capture.jsonl')
                           if r.get('rec') == 'game_message' and r.get('data_id') == 0x44
                           and r.get('t', 0) > since[i]]
                if replies:
                    if not replies[0].get('fields', {}).get('isOK'):
                        raise RuntimeError(f'Helper {i+1} was refused; not retrying a refusal')
                    pending.remove(i)
            if pending: time.sleep(.05)
        if not pending: return
        if attempt < 2:
            for i in pending: write(folders[i], [commands[i]])
    raise TimeoutError('Excavation permission not received')


def wait(folders, expected, deliveries, deadline):
    while time.monotonic() < deadline:
        finished = True
        for folder, wanted, hits in zip(folders, expected, deliveries):
            rows = read(folder/'capture.jsonl')
            if any(r.get('rec') in ('end', 'dig_attack_stopped') for r in rows):
                raise RuntimeError(f'{folder.name}: transport stopped')
            messages = [r for r in rows if r.get('rec') == 'game_message']
            permission_time = max((r.get('t', 0) for r in messages if r.get('data_id') == 0x44
                                   and (r.get('fields') or {}).get('isOK')), default=0)
            if any(r.get('rec') == 'dig_wall_batch_cancelled' and r.get('t', 0) > permission_time
                   for r in rows):
                raise RuntimeError('Wall closed; queued attacks discarded')
            if messages and messages[-1].get('data_id') == 0x28:
                raise RuntimeError('Wall ended')
            acked = [r for r in rows if r.get('rec') == 'dig_attack_delivery']
            if any(not r.get('reliable_acked') for r in acked):
                raise RuntimeError('An attack was not acknowledged')
            finished &= (sum(r.get('rec') == 'inject_sent' for r in rows) >= wanted
                         and len(acked) >= hits)
        if finished: return
        time.sleep(.01)
    raise TimeoutError('Coordinated sweep deadline expired')


def sweep_session(session, tool='pickaxe', join=False):
    from types import SimpleNamespace
    args = SimpleNamespace(session=Path(session), tool=tool, join=join)
    folders = [args.session/f'helper-{i}' for i in (1, 2)]
    if args.join: join_current_wall(folders)
    states = [read(p/'capture.jsonl') for p in folders]
    walls = []
    for rows in states:
        messages = [r for r in rows if r.get('rec') == 'game_message']
        permissions = [r for r in messages if r.get('data_id') == 0x44]
        if not permissions or not permissions[-1].get('fields', {}).get('isOK'):
            raise RuntimeError('Both helpers must have a positive excavation permission')
        wall = [r for r in messages if r.get('data_id') == 0x19][-1]
        if permissions[-1]['t'] < wall['t']:
            raise RuntimeError('Request fresh permissions for the current wall before sweeping')
        if any(r.get('data_id') == 0x28 and r['t'] > permissions[-1]['t'] for r in messages):
            raise RuntimeError('Wall ended after permission')
        walls.append((wall['fields']['zoneID'], wall['fields']['MainPos']))
    if walls[0] != walls[1]: raise RuntimeError('Helpers are in different walls')
    counts = [sum(r.get('rec') == 'inject_sent' for r in rows) for rows in states]
    hits = [sum(r.get('rec') == 'dig_attack_delivery' for r in rows) for rows in states]
    for folder in folders: write(folder, ['0x04:0e00'])
    counts = [v+1 for v in counts]
    wait(folders, counts, hits, time.monotonic()+10)
    routes = partition_cells()
    start = time.monotonic()
    for folder, route in zip(folders, routes): write(folder, [attack(route[0], args.tool == 'hammer')])
    counts = [v+1 for v in counts]; hits = [v+1 for v in hits]
    wait(folders, counts, hits, time.monotonic()+10)
    for folder, route in zip(folders, routes):
        commands = [attack(cell, args.tool == 'hammer') for cell in route for _ in range(8)]
        write(folder, commands[1:])
    counts = [v+519 for v in counts]; hits = [v+519 for v in hits]
    wait(folders, counts, hits, time.monotonic()+20)
    result = dict(helpers=2, tool=args.tool, cells_per_helper=65, overlapping_cells=0, hits_per_cell=8,
                  seconds_to_ack=time.monotonic()-start, messages=1040,
                  physical_clearance_confirmed=False, wall=walls[0])
    (args.session/'pair-sweep-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--tool', choices=('pickaxe', 'hammer'), default='pickaxe')
    parser.add_argument('--join', action='store_true', help='Request coordinates and join each helper once')
    args = parser.parse_args()
    sweep_session(args.session, args.tool, args.join)


if __name__ == '__main__': main()
