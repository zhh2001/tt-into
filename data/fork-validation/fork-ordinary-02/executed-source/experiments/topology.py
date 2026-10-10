"""仅清理本次创建的 Mininet 链路和 BMv2 子进程。"""
from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import time

from mininet.link import Link
from mininet.net import Mininet
from mininet.node import Switch


class Bmv2Switch(Switch):
    def __init__(self, name, *, program, artifact_dir, thrift_port, device_id, **params):
        super().__init__(name, **params)
        self.program = Path(program)
        self.artifact_dir = Path(artifact_dir)
        self.thrift_port = thrift_port
        self.device_id = device_id
        self.process = None
        self.log = None

    def start(self, controllers):
        self.artifact_dir.mkdir()
        self.log = open(self.artifact_dir / "switch.log", "w")
        command = ["simple_switch", "--thrift-port", str(self.thrift_port),
                   "--device-id", str(self.device_id), "--pcap=" + str(self.artifact_dir),
                   "--notifications-addr", "ipc://" + str(self.artifact_dir / "notifications.ipc"),
                   "--log-console", "-L", "warn"]
        for port, interface in sorted(self.intfs.items()):
            if port:
                command += ["-i", f"{port}@{interface.name}"]
        command.append(str(self.program))
        self.process = self.popen(command, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.name} 启动失败，见 {self.artifact_dir / 'switch.log'}")
            try:
                with socket.create_connection(("127.0.0.1", self.thrift_port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"{self.name} 的 Thrift 启动超时")

    def cli(self, commands: list[str]) -> str:
        result = subprocess.run(["simple_switch_CLI", "--thrift-port", str(self.thrift_port)],
                                input="\n".join(commands) + "\n", text=True,
                                capture_output=True, timeout=20)
        output = result.stdout + result.stderr
        with open(self.artifact_dir / "control.log", "a") as handle:
            handle.write("\n".join(commands) + "\n" + output + "\n")
        if result.returncode or "Error" in output or "Invalid" in output:
            raise RuntimeError(f"{self.name} 控制操作失败：{output[-1800:]}")
        return output

    def stop(self, deleteIntfs=True):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=3)
        if self.log:
            self.log.close()
        super().stop(deleteIntfs=deleteIntfs)


def create_chain(program: Path, artifact_dir: Path, hops: int, prefix: str,
                 base_port: int, device_base: int):
    network = Mininet(controller=None, link=Link, build=False, autoSetMacs=False)
    try:
        source = network.addHost(prefix + "a", ip="10.0.0.1/24", mac="02:00:00:00:00:01")
        destination = network.addHost(prefix + "b", ip="10.0.0.2/24", mac="02:00:00:00:00:02")
        collector = network.addHost(prefix + "c", ip="10.0.0.3/24", mac="02:00:00:00:00:03")
        switches = [network.addSwitch(prefix + f"s{index}", cls=Bmv2Switch,
                     program=program, artifact_dir=artifact_dir / f"node-{index}",
                     thrift_port=base_port + index, device_id=device_base + index)
                    for index in range(1, hops + 2)]
        network.addLink(source, switches[0], port1=0, port2=1)
        for left, right in zip(switches, switches[1:]):
            network.addLink(left, right, port1=2, port2=1)
        network.addLink(switches[-1], destination, port1=2, port2=0)
        network.addLink(switches[-1], collector, port1=3, port2=0)
        network.build()
        for node in network.hosts:
            node.cmd("sysctl -q -w net.ipv6.conf.all.disable_ipv6=1")
        for node in network.hosts + switches:
            for interface in node.intfList():
                if interface.name != "lo":
                    node.cmd(f"ethtool -K {interface.name} rx off tx off tso off gso off gro off 2>/dev/null")
                    node.cmd(f"ip link set dev {interface.name} mtu 1500")
        source.setARP("10.0.0.2", "02:00:00:00:00:02")
        destination.setARP("10.0.0.1", "02:00:00:00:00:01")
        network.start()
        return network, source, destination, collector, switches
    except BaseException:
        network.stop()
        raise
