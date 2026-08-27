import asyncio
import subprocess


def get_wsl_ip() -> str:
    try:
        out = subprocess.check_output(["wsl", "-d", "Ubuntu-24.04", "--", "hostname", "-I"], text=True)
        return out.split()[0]
    except (subprocess.SubprocessError, OSError, IndexError):
        return "127.0.0.1"


TARGET_HOST = get_wsl_ip()
TARGET_PORT = 6379


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        remote_reader, remote_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except OSError:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass
        return

    async def forward(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                w.write(data)
                await w.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                w.close()
                await w.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    await asyncio.gather(
        forward(reader, remote_writer),
        forward(remote_reader, writer),
        return_exceptions=True,
    )

async def main() -> None:
    global TARGET_HOST
    TARGET_HOST = get_wsl_ip()
    server = await asyncio.start_server(handle_client, "127.0.0.1", 6379, reuse_address=True)
    print(f"Redis proxy listening on 127.0.0.1:6379 -> {TARGET_HOST}:6379", flush=True)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
