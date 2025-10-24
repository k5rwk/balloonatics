import asyncio
import argparse
import udp_client
import cbor2
import json
import aiohttp
import os

BATCH_SIZE = 15
BATCH_TIMEOUT = 5 # seconds

RETRY_ATTEMPTS = 10
RETRY_DELAY = 4

API_KEY = "juRD8E/uTbWD4NXVEP9Lgt1NzxKi/tGupgBvKT+tktc="

# Incoming UDP packets
packet_queue = asyncio.Queue(150)
# Queue of data dictionaries to be inserted into the database
data_queue = asyncio.Queue(150)

async def send_data(data: list[dict]):
    async with aiohttp.ClientSession() as session:
        url = "https://api.k5nbc.com/balloon/telemetry/"
        headers = {
            'X-API-KEY': API_KEY
        }
        for _ in range(RETRY_ATTEMPTS):
            try:
                async with session.put(url, json=data, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status >= 200 and resp.status < 300:
                        print(f"Successfully sent {len(data)} items, status={resp.status}")
                        break
                    if resp.status == 400:
                        print(f"Client error, not retrying. status={resp.status}, body={text}")
                        break
                    else:
                        print(f"Failed to send data, status={resp.status}, body={text}")
            except Exception as e:
                print(f"Error sending data: {e}")
            # Wait for retry
            await asyncio.sleep(RETRY_DELAY)
            print("Retrying...")

async def gather_and_send():
    while True:
        data_to_send = []
        try:
            async with asyncio.timeout(BATCH_TIMEOUT):
                for _ in range(BATCH_SIZE):
                    item = await data_queue.get()
                    data_to_send.append(item)

        except asyncio.TimeoutError:
            print(f"Sending {len(data_to_send)} items after timeout")
            
        if not data_to_send:
            continue
         
        await send_data(data_to_send)

async def process_packets():
    while True:
        packet = await packet_queue.get()
        packet = json.loads(packet.decode())
    
        if packet.get('type') == 'WENET' and packet.get('packet', [None, None])[0:2] == [3, 55]:
            # Decode CBOR data
            try:
                cbor_data = cbor2.loads(bytearray(packet['packet'][2:]))
            except Exception as e:
                print(f"Error decoding CBOR data: {e}")
                continue
            # Add reporter callsign
            cbor_data['reporter_id'] = args.callsign
            # print("Decoded CBOR data:", cbor_data)
            data_queue.put_nowait(cbor_data)

async def main(args: argparse.Namespace):
    tasks = []
    tasks.append(asyncio.create_task(udp_client.run_client(packet_queue, args.port)))
    tasks.append(asyncio.create_task(process_packets()))
    tasks.append(asyncio.create_task(gather_and_send()))
    await asyncio.gather(*(tasks))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--callsign', type=str, default=os.getenv('MYCALL', 'NO_CALLSIGN'), help='Callsign for telemetry data')
    parser.add_argument('--port', type=int, default=55672, help='UDP port to listen on')
    args = parser.parse_args()
    asyncio.run(main(args))
