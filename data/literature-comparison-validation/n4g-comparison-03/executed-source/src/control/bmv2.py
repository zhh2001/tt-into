"""复用本机 BMv2 的 Thrift 绑定，保持连接并记录逐操作边界。"""
import ast
import importlib
from pathlib import Path
import shutil
import sys
import time


def bindings():
    try:
        return importlib.import_module('bm_runtime.standard.Standard')
    except ModuleNotFoundError:
        wrapper = shutil.which('simple_switch_CLI')
        if not wrapper:
            raise RuntimeError('未找到 BMv2 CLI 及 Python 绑定')
        # 安装器的包装脚本记录依赖路径，只读取常量，不执行脚本。
        tree = ast.parse(Path(wrapper).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'append':
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    directory = node.args[0].value
                    if (Path(directory) / 'bm_runtime').is_dir():
                        sys.path.append(directory)
        return importlib.import_module('bm_runtime.standard.Standard')


class Client:
    def __init__(self, port, node, log):
        standard = bindings()
        from thrift.transport import TSocket, TTransport
        from thrift.protocol import TBinaryProtocol, TMultiplexedProtocol
        self.types = importlib.import_module('bm_runtime.standard.ttypes')
        self.transport = TTransport.TBufferedTransport(TSocket.TSocket('127.0.0.1', port))
        self.transport.open()
        protocol = TMultiplexedProtocol.TMultiplexedProtocol(TBinaryProtocol.TBinaryProtocol(self.transport), 'standard')
        self.api = standard.Client(protocol)
        self.node, self.log = node, log
        self.handles = {}

    def call(self, method, *args, kind='installation'):
        before = time.monotonic_ns()
        value = getattr(self.api, method)(0, *args)
        after = time.monotonic_ns()
        def clean(x):
            if isinstance(x, bytes): return x.hex()
            if isinstance(x, (list, tuple)): return [clean(v) for v in x]
            if hasattr(x, '__dict__'): return {k: clean(v) for k, v in x.__dict__.items()}
            return x
        self.log.append({'node': self.node, 'method': method, 'arguments': clean(args),
                         'before_monotonic_ns': before, 'after_monotonic_ns': after, 'kind': kind})
        return value

    def read(self, name, index, kind='online_observation'):
        return self.call('bm_register_read', name, index, kind=kind)

    def read_all(self, name):
        return self.call('bm_register_read_all', name, kind='diagnostic_read')

    def write(self, name, index, value, kind='installation'):
        return self.call('bm_register_write', name, index, value, kind=kind)

    def reset(self, name):
        self.call('bm_register_reset', name, kind='cold_start_reset')

    @staticmethod
    def encode(fields):
        return [int(value).to_bytes((bits + 7) // 8, 'big') for value, bits in fields]

    def default(self, table, action, fields, kind='installation'):
        self.call('bm_mt_set_default_action', table, action, self.encode(fields), kind=kind)

    def entry(self, table, key, bits, action, fields, kind='installation'):
        data = self.encode(fields)
        label = (table, key)
        if label in self.handles:
            self.call('bm_mt_modify_entry', table, self.handles[label], action, data, kind=kind)
        else:
            match = self.types.BmMatchParam(type=self.types.BmMatchParamType.EXACT,
                                          exact=self.types.BmMatchParamExact(self.encode([(key, bits)])[0]))
            self.handles[label] = self.call('bm_mt_add_entry', table, [match], action, data,
                                            self.types.BmAddEntryOptions(), kind=kind)

    def close(self):
        self.transport.close()
