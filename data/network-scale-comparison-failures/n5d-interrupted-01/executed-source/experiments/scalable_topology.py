"""依据固定拓扑合同建立合流链，源入口与流槽分开配置。"""
from mininet.link import Link
from mininet.net import Mininet
from topology import Bmv2Switch


def create_network(spec, program, artifact_dir, prefix, base_port):
    network = Mininet(controller=None, link=Link, build=False, autoSetMacs=False)
    try:
        hosts = {name: network.addHost(prefix + suffix, ip=ip + '/24', mac=mac)
                 for name, suffix, ip, mac in (
                     ('source', 'a', '10.0.0.1', '02:00:00:00:00:01'),
                     ('destination', 'b', '10.0.0.2', '02:00:00:00:00:02'),
                     ('collector', 'c', '10.0.0.3', '02:00:00:00:00:03'))}
        switches = {n: network.addSwitch(prefix + f's{n}', cls=Bmv2Switch, program=program,
            artifact_dir=artifact_dir / f'node-{n}', thrift_port=base_port + n, device_id=900 + n) for n in spec.nodes}
        vertices = dict(hosts, **{str(n): s for n, s in switches.items()})
        for left, lp, right, rp in spec.physical_links:
            network.addLink(vertices[str(left)], vertices[str(right)], port1=lp, port2=rp)
        network.build()
        for host in network.hosts:
            host.cmd('sysctl -q -w net.ipv6.conf.all.disable_ipv6=1')
        for node in network.hosts + list(switches.values()):
            for interface in node.intfList():
                if interface.name != 'lo':
                    node.cmd(f'ethtool -K {interface.name} rx off tx off tso off gso off gro off 2>/dev/null')
                    node.cmd(f'ip link set dev {interface.name} mtu 1500')
        hosts['destination'].setARP('10.0.0.1', '02:00:00:00:00:01')
        network.start()
        return network, hosts, switches
    except BaseException:
        network.stop()
        raise


def configure(spec, clients, switches):
    for node, client in clients.items():
        client.default('TTIngress.node_config', 'TTIngress.set_node',
            [(node, 8), (int(node in (1, 2)), 1), (int(node == spec.record['sink']), 1), (1500, 16)])
        client.default('TTIngress.ipv4_forward', 'TTIngress.forward',
            [(2, 9), (0x020000000002, 48), (0x020000100000 + node, 48)])
        for f in spec.record['flows']:
            client.register_flow(f['sport'], f['slot'], f['identity']['tag'], f['identity']['session'])
    switches[spec.record['sink']].cli(['mirroring_add 100 3'])


def cold(spec, clients):
    for node in spec.nodes:
        if node == spec.record['sink']:
            continue
        for name in ('last_timestamp', 'has_sample', 'max_count', 'time_threshold'):
            clients[node].reset('TTEgress.' + name)
        clients[node].reset('TTIngress.packet_count')
