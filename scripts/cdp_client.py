"""Small dependency-free CDP client for integration tests with the installed Edge browser."""
import base64
import json
import os
import socket
import struct
from urllib.parse import urlsplit


class BrowserCDP:
    def __init__(self, url):
        target = urlsplit(url)
        self.sock = socket.create_connection((target.hostname, target.port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET {target.path} HTTP/1.1\r\nHost: {target.netloc}\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        data = b''
        while b'\r\n\r\n' not in data:
            data += self.sock.recv(4096)
        header, self.buffer = data.split(b'\r\n\r\n', 1)
        assert header.split(b' ', 2)[1] == b'101', header[:100]
        self.sequence, self.events = 0, []

    def read(self, size):
        while len(self.buffer) < size:
            block = self.sock.recv(max(4096, size-len(self.buffer)))
            if not block:
                raise ConnectionError('Browser disconnected')
            self.buffer += block
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result

    def call(self, method, params=None, timeout=20):
        self.sequence += 1
        data = json.dumps({'id': self.sequence, 'method': method, 'params': params or {}}).encode()
        mask = os.urandom(4)
        header = bytes([129, 128 | len(data)]) if len(data) < 126 else bytes([129, 254]) + struct.pack('!H', len(data))
        self.sock.sendall(header+mask+bytes(v ^ mask[i % 4] for i, v in enumerate(data)))
        self.sock.settimeout(timeout)
        while True:
            opcode, raw_length = self.read(2)
            length = raw_length & 127
            if length == 126:
                length = struct.unpack('!H', self.read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self.read(8))[0]
            assert length < 8000000
            mask = self.read(4) if raw_length & 128 else None
            payload = self.read(length)
            if mask:
                payload = bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
            if opcode & 15 == 8:
                raise ConnectionError('Browser closed')
            if opcode & 15 != 1:
                continue
            message = json.loads(payload)
            if message.get('id') == self.sequence:
                if 'error' in message:
                    raise RuntimeError(message['error']['message'])
                return message.get('result', {})
            self.events.append(message)

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(str(result['exceptionDetails']))
        return result['result'].get('value')
