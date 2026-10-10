"""三条固定业务路径，两条分支共享合流出口。"""
from mininet.link import Link
from mininet.net import Mininet
from topology import Bmv2Switch


def create_fork(program, artifact_dir, prefix='pf', base_port=9470):
    network = Mininet(controller=None, link=Link, build=False, autoSetMacs=False)
    try:
        source = network.addHost(prefix + 'a', ip='10.0.0.1/24', mac='02:00:00:00:00:01')
        destination = network.addHost(prefix + 'b', ip='10.0.0.2/24', mac='02:00:00:00:00:02')
        collector = network.addHost(prefix + 'c', ip='10.0.0.3/24', mac='02:00:00:00:00:03')
        switches = [network.addSwitch(prefix + f's{i}', cls=Bmv2Switch, program=program,
                                     artifact_dir=artifact_dir / f'node-{i}', thrift_port=base_port + i,
                                     device_id=700 + i) for i in range(1, 5)]
        network.addLink(source, switches[0], port1=0, port2=1)
        network.addLink(source, switches[1], port1=1, port2=1)
        network.addLink(source, switches[2], port1=2, port2=4)
        network.addLink(switches[0], switches[2], port1=2, port2=1)
        network.addLink(switches[1], switches[2], port1=2, port2=3)
        network.addLink(switches[2], switches[3], port1=2, port2=1)
        network.addLink(switches[3], destination, port1=2, port2=0)
        network.addLink(switches[3], collector, port1=3, port2=0)
        network.build()
        for host in network.hosts:
            host.cmd('sysctl -q -w net.ipv6.conf.all.disable_ipv6=1')
        for node in network.hosts + switches:
            for interface in node.intfList():
                if interface.name != 'lo':
                    node.cmd(f'ethtool -K {interface.name} rx off tx off tso off gso off gro off 2>/dev/null')
                    node.cmd(f'ip link set dev {interface.name} mtu 1500')
        destination.setARP('10.0.0.1', '02:00:00:00:00:01')
        network.start()
        return network, source, destination, collector, switches
    except BaseException:
        network.stop()
        raise
