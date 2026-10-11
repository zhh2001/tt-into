"""逐 RPC 统计实际序列化请求和回复长度，不保留载荷。"""
import importlib
from control.bmv2 import Client, bindings


class MeteredClient(Client):
    def __init__(self,port,node,log):
        standard=bindings()
        from thrift.transport import TSocket,TTransport
        from thrift.protocol import TBinaryProtocol,TMultiplexedProtocol
        class CounterSocket(TSocket.TSocket):
            sent=0
            received=0
            def write(self,buf):
                super().write(buf)
                self.sent+=len(buf)
            def read(self,size):
                value=super().read(size)
                self.received+=len(value)
                return value
        self.socket=CounterSocket('127.0.0.1',port)
        self.transport=TTransport.TBufferedTransport(self.socket)
        self.transport.open()
        protocol=TMultiplexedProtocol.TMultiplexedProtocol(TBinaryProtocol.TBinaryProtocol(self.transport),'standard')
        self.api=standard.Client(protocol)
        self.types=importlib.import_module('bm_runtime.standard.ttypes')
        self.node,self.log=node,log
        self.handles={}

    def call(self,method,*args,kind='installation'):
        sent,received=self.socket.sent,self.socket.received
        value=super().call(method,*args,kind=kind)
        self.log[-1].update(request_serialized_bytes=self.socket.sent-sent,
                            reply_serialized_bytes=self.socket.received-received)
        return value
