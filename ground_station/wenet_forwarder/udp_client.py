
import sys
import asyncio

class UDPClientProtocol(asyncio.DatagramProtocol):
    def __init__(self, receive_queue, on_con_lost):
        self.transport = None
        self.on_con_lost = on_con_lost
        self.receive_queue = receive_queue

    def connection_made(self, transport):
        self.transport = transport
        print('UDP connection established')

    def error_received(self, exc):
        print('Error received:', exc)

    def send_packet(self, data):
        if self.transport is not None:
            self.transport.sendto(data)
        else:
            print("Transport is not available")

    def datagram_received(self, data, addr):
        self.receive_queue.put_nowait(data)

    def close_conn(self):
        if self.transport is not None:
            print("Closing UDP connection")
            self.transport.close()
        else:
            print("Transport is not available")
            
    def connection_lost(self, exc):
        self.on_con_lost.set_result(True)
        print("UDP connection closed")

async def run_client(receive_queue, port):
    loop = asyncio.get_event_loop()
    on_con_lost = loop.create_future()
    print("Starting UDP client")
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPClientProtocol(receive_queue, on_con_lost),
        local_addr=('0.0.0.0', port)
    )
    try:
        while True:
            await asyncio.sleep(1)
    finally:
        protocol.close_conn()
        await on_con_lost

if __name__ == "__main__":
    print("This script is not meant to be run directly.")
    sys.exit(1)

