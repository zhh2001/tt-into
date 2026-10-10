"""用本机绑定核对 API 序列化长度，不建立网络连接。"""
import hashlib
import json
from pathlib import Path

from control.bmv2 import bindings
from orchestration.event_schema import RpcCost


def calibration(root):
    standard = bindings()
    from thrift.transport.TTransport import TMemoryBuffer
    from thrift.protocol import TBinaryProtocol, TMultiplexedProtocol
    from thrift.Thrift import TMessageType
    rows = {}
    specifications = [('enabled', 'bm_register_write', 'TTEgress.max_count'),
                      ('threshold_us', 'bm_register_write', 'TTEgress.time_threshold'),
                      ('read', 'bm_register_read', 'TTIngress.packet_count')]
    for key, method, register in specifications:
        request = TMemoryBuffer()
        client = standard.Client(TMultiplexedProtocol.TMultiplexedProtocol(TBinaryProtocol.TBinaryProtocol(request), 'standard'))
        args = (0, register, 0, 0) if method.endswith('write') else (0, register, 0)
        getattr(client, 'send_' + method)(*args)
        reply = TMemoryBuffer()
        protocol = TBinaryProtocol.TBinaryProtocol(reply)
        protocol.writeMessageBegin(method, TMessageType.REPLY, 0)
        result = getattr(standard, method + '_result')(**({} if method.endswith('write') else {'success': 0}))
        result.write(protocol)
        protocol.writeMessageEnd()
        rows[key] = {'request_bytes': len(request.getvalue()), 'reply_bytes': len(reply.getvalue()),
                     'provenance': '本机 BMv2 Standard 绑定的 TBinary/TMultiplexed 序列化长度'}
    old = root / 'data/admission-validation/admission-margin-ordinary-01/rpc-operations.jsonl'
    reference = {register: set() for _, method, register in specifications if method.endswith('write')}
    for line in old.read_text().splitlines():
        row = json.loads(line)
        name = row.get('arguments', [None])[0]
        if row.get('method') == 'bm_register_write' and name in reference:
            reference[name].add((row['request_serialized_bytes'], row['reply_serialized_bytes']))
    for key, method, name in specifications:
        if method.endswith('write') and reference[name] != {(rows[key]['request_bytes'], rows[key]['reply_bytes'])}:
            raise ValueError('本机序列化与归档的实际写 RPC 长度不一致')
    return {'calls': rows, 'binding_file_sha256': hashlib.sha256(Path(standard.__file__).read_bytes()).hexdigest(),
            'historical_write_source': str(old.relative_to(root)),
            'historical_write_source_sha256': hashlib.sha256(old.read_bytes()).hexdigest(),
            'scope': '写费用与既有实测相容，读费用是当前绑定序列化参照，不是本轮网络测量'}


def costs(record):
    return {key: RpcCost(**value) for key, value in record['calls'].items()}
