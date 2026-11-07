import asyncio


class SocksHttpBridge:
    """Локальный HTTP-прокси без пароля. Наружу ходит через один SOCKS5 с логином."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.server: asyncio.Server | None = None
        self.local_port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        socket = self.server.sockets[0]
        self.local_port = socket.getsockname()[1]

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        remote_writer = None
        try:
            request = await client_reader.readuntil(b"\r\n\r\n")
            first = request.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
            method, target, _proto = first.split(" ", 2)
            if method.upper() != "CONNECT":
                client_writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                await client_writer.drain()
                return
            host, port_text = target.rsplit(":", 1)
            remote_reader, remote_writer = await self._open_socks(host, int(port_text))
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            await asyncio.gather(
                self._pipe(client_reader, remote_writer),
                self._pipe(remote_reader, client_writer),
            )
        except Exception:
            try:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await client_writer.drain()
            except Exception:
                pass
        finally:
            client_writer.close()
            if remote_writer:
                remote_writer.close()

    async def _open_socks(self, host: str, port: int):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout=12)
        writer.write(b"\x05\x01\x02")
        await writer.drain()
        answer = await reader.readexactly(2)
        if answer != b"\x05\x02":
            raise RuntimeError("SOCKS5 не принял логин")
        user = self.username.encode()
        password = self.password.encode()
        writer.write(bytes((0x01, len(user))) + user + bytes((len(password),)) + password)
        await writer.drain()
        auth = await reader.readexactly(2)
        if auth[1] != 0:
            raise RuntimeError("SOCKS5 отклонил логин или пароль")
        host_bytes = host.encode()
        writer.write(b"\x05\x01\x00\x03" + bytes((len(host_bytes),)) + host_bytes + port.to_bytes(2, "big"))
        await writer.drain()
        head = await reader.readexactly(4)
        if head[1] != 0:
            raise RuntimeError(f"SOCKS5 не открыл сайт, код {head[1]}")
        if head[3] == 1:
            await reader.readexactly(4 + 2)
        elif head[3] == 4:
            await reader.readexactly(16 + 2)
        elif head[3] == 3:
            size = (await reader.readexactly(1))[0]
            await reader.readexactly(size + 2)
        return reader, writer

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
