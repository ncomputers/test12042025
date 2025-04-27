# websocket_client.py

import asyncio
import websockets
import json
import logging
import argparse
import sys

class WebSocketClient:
    """
    Connects to a WebSocket URI and subscribes to a single channel.
    Yields incoming JSON-decoded messages.
    """
    def __init__(self, uri: str, channel: str, max_retries: int = 5, retry_delay: int = 5):
        self.uri = uri
        self.channel = channel
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def connect(self):
        retry = 0
        while retry < self.max_retries:
            try:
                async with websockets.connect(self.uri) as ws:
                    await ws.send(json.dumps({"type": "reg", "channel": self.channel}))
                    logging.info(f"Subscribed to: {self.channel}")
                    async for raw in ws:
                        try:
                            yield json.loads(raw)
                        except json.JSONDecodeError:
                            logging.warning(f"[{self.channel}] Non-JSON message skipped")
                return
            except Exception as e:
                retry += 1
                logging.warning(f"[{self.channel}] Connection error ({e}); retry {retry}/{self.max_retries}")
                await asyncio.sleep(self.retry_delay)
        logging.error(f"[{self.channel}] Max retries reached; exiting.")
        sys.exit(1)

async def _worker(uri, channel, queue):
    client = WebSocketClient(uri, channel)
    async for msg in client.connect():
        await queue.put((channel, msg))

async def test_channels(uri, channels, limit):
    """
    Opens a separate WS connection per channel,
    prints `limit` messages from each, then exits.
    """
    queue = asyncio.Queue()
    tasks = [asyncio.create_task(_worker(uri, ch, queue)) for ch in channels]
    counts = {ch: 0 for ch in channels}
    total_needed = limit * len(channels)
    received = 0

    while received < total_needed:
        channel, msg = await queue.get()
        counts[channel] += 1
        received += 1
        print(f"\n--- [{channel}] Message #{counts[channel]} ---")
        print(json.dumps(msg, indent=2))

    for t in tasks:
        t.cancel()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test multiple WebSocket channels")
    parser.add_argument(
        "--uri",
        default="wss://ws-sgp.tensorcharts.com/tensorWS",
        help="WebSocket URI"
    )
    parser.add_argument(
        "--channels",
        nargs="+",
        default=[
            "bitfinexBTCUSD",
            "binanceBTCUSDT",
            "bitstampBTCUSD",
            "deribitBTC-PERPETUAL",
            "gdaxBCH-USD"
        ],
        help="Channels to subscribe to (default: all)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of messages per channel before exiting"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    try:
        asyncio.run(test_channels(args.uri, args.channels, args.limit))
    except KeyboardInterrupt:
        logging.info("Interrupted by user, shutting down.")
