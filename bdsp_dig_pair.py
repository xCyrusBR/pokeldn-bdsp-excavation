"""Experimental: two separately identified Pia participants over one ESP32 LDN connection.

This does not claim two accepted players until both handshakes and permissions succeed.
No attacks are sent automatically. Each participant owns an independent inject file.
"""
import argparse
import contextlib
import importlib.util
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE/'bdsp_connect.py').exists() else HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'vendor/LDN'))

import trio
import ldn
from pokeldn.bdsp import COMM_ID, PASSPHRASE
from pokeldn.ldn.transport import find_ap_phy
from pokeldn.ldn import userspace_ip


def unused_endpoint_ip(parts, our_ip):
    used = {str(p.ip_address) for p in parts if getattr(p, 'connected', False)}
    prefix = our_ip.rsplit('.', 1)[0]
    return next(f'{prefix}.{i}' for i in range(2, 255)
                if f'{prefix}.{i}' not in used and f'{prefix}.{i}' != our_ip)


def partition_cells():
    """Opposite corners toward the middle, all 130 cells covered exactly once."""
    path = [(col, row) for col in range(13)
            for row in (range(10) if col % 2 == 0 else range(9, -1, -1))]
    return path[:65], list(reversed(path[65:]))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def forward_discovery(source, endpoint):
    """Port 12345 broadcasts bootstrap an otherwise independent port-12346 peer."""
    from pokeldn.ldn.pia5 import PiaHeader5, is_pia5
    while True:
        await trio.lowlevel.wait_readable(source)
        try:
            data, addr = source.recvfrom(4096)
        except BlockingIOError:
            continue
        if is_pia5(data) and PiaHeader5.parse(data).dst_var in (0, 1, 0xffffffff):
            endpoint._push((data, addr))


async def stop_after(nursery, seconds):
    await trio.sleep(seconds)
    nursery.cancel_scope.cancel()


async def run_controller(nursery, controller, output, seconds):
    await controller(output, seconds)
    nursery.cancel_scope.cancel()


class AttachedLDN:
    def __init__(self, net, network): self.net, self.network = net, network
    def __getattr__(self, name): return getattr(ldn, name)
    async def scan(self, *args, **kwargs): return [self.net]
    @contextlib.asynccontextmanager
    async def connect(self, param): yield self.network


async def run(args):
    phy = find_ap_phy(log=print)
    keys = ldn.load_keys(args.keys)
    nets = await ldn.scan(keys, phyname=phy, channels=[1,6,11], dwell_time=2)
    net = next((n for n in nets if n.local_communication_id == COMM_ID
                and n.scene_id in (0x3110,0x3120,0x3140)), None)
    if net is None:
        print('No BDSP Underground session found')
        return 3
    param = ldn.ConnectNetworkParam()
    param.keys, param.network, param.password = keys, net, PASSPHRASE
    param.name, param.app_version = b'DigPair', net.app_version
    param.phyname, param.ifname = phy, 'ldnclient'
    args.output.mkdir(parents=True, exist_ok=True)
    async with ldn.connect(param) as network:
        parts = list(network.info().participants)
        ours = next(p for p in parts[1:] if getattr(p, 'connected', False))
        physical_mac = bytes(ours.mac_address)
        virtual_mac = bytearray(physical_mac)
        virtual_mac[0] ^= 2
        virtual_mac[-1] ^= 1
        stack = userspace_ip.lookup('ldnclient')
        alias_ip = None
        if args.endpoint_mode.startswith('alias-'):
            alias_ip = unused_endpoint_ip(parts, str(ours.ip_address))
            alias_mac = physical_mac if args.endpoint_mode == 'alias-physical' else bytes(virtual_mac)
            stack.register_alias(alias_ip, alias_mac)
            print(f'[pair] Independent endpoint {alias_ip}, L2={alias_mac.hex()}, mode={args.endpoint_mode}')
        async with trio.open_nursery() as nursery:
            if args.seconds:
                nursery.start_soon(stop_after, nursery, args.seconds)
            for index, identity in enumerate((physical_mac, bytes(virtual_mac))[:args.helpers]):
                folder = args.output/f'helper-{index+1}'
                folder.mkdir(exist_ok=True)
                if (folder/'capture.jsonl').exists():
                    raise ValueError('Use a fresh output folder')
                (folder/'inject.txt').write_text('')
                transport = ROOT/'bin/bdsp_connect.py'
                if not transport.exists(): transport = ROOT/'bdsp_connect.py'
                module = load(f'bdsp_pair_{index}', transport)
                module.ldn = AttachedLDN(net, network)
                module.cleanup = lambda: None
                config = module.build_parser().parse_args([
                    '--keys', args.keys, '--name', f'Cavar-{index+1}',
                    '--src-var', hex(secrets.randbelow(0xfffffffe)+1),
                    '--identity-mac', identity.hex(), '--filter-pia-destination',
                    '--expected-scene', hex(net.scene_id), '--hold', str(args.seconds or float('inf')),
                    '--count', '9', '--connect', '3', '--join', '4', '--reliable-ack',
                    '--answer-requests', '--ug-join', '--room-walk-steps', '0',
                    '--join-offset-x', '0', '--join-offset-z', '0',
                    '--capture', str(folder/'capture.jsonl'),
                    '--inject-file', str(folder/'inject.txt'),
                    '--dig-fast', '--dig-burst-size', '32', '--dig-attack-interval', '.01'])
                if index == 1 and alias_ip:
                    config.endpoint_ip = alias_ip
                    config.endpoint_mac = alias_mac
                    config.endpoint_socket = stack.udp_socket(12345, local_ip=alias_ip)
                    config.endpoint_socket.setblocking(False)
                elif index == 1 and args.endpoint_mode == 'port':
                    config.endpoint_port = 12346
                    config.endpoint_socket = stack.udp_socket(12346)
                    config.endpoint_socket.setblocking(False)
                    discovery = stack.udp_socket(12345)
                    discovery.setblocking(False)
                    nursery.start_soon(forward_discovery, discovery, config.endpoint_socket)
                if getattr(args, 'event_sink', None):
                    config.event_sink = lambda event, seat=index: args.event_sink(seat, event)
                nursery.start_soon(module.main_async, config)
                await trio.sleep(2)
            if getattr(args, 'controller', None):
                nursery.start_soon(run_controller, nursery, args.controller, args.output, args.seconds)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keys')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--helpers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--endpoint-mode', choices=('shared', 'port', 'alias-physical', 'alias-virtual'),
                        default='port')
    args = parser.parse_args()
    if not args.keys:
        from tools.pokeldn_manager.runtime import find_prod_keys
        args.keys = str(find_prod_keys())
    return trio.run(run, args)


if __name__ == '__main__': raise SystemExit(main())
