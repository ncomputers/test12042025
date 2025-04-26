#%%
import asyncio
import json
import os

from websocket_client import WebSocketClient

# In Jupyter, allow nested loops
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

#%%
async def stream_candles(uri: str, channel: str, target_count: int = 500, outfile: str = "sample_candles.json"):
    """
    Connects to the WS, prints each 'candle' message as JSON,
    and writes each line to outfile one by one until target_count is reached.
    """
    os.makedirs(os.path.dirname(outfile) or ".", exist_ok=True)
    count = 0
    # open in write mode to start fresh
    with open(outfile, "w") as f:
        async for msg in WebSocketClient(uri=uri, channel=channel).connect():
            if msg.get("type") != "candle":
                continue
            data = msg["data"]
            line = json.dumps(data)
            print(line)               # print each line as it arrives
            f.write(line + "\n")      # write one JSON object per line
            count += 1
            if count >= target_count:
                break
    print(f"Saved {count} candle entries to {outfile}")

#%%
# Parameters
URI          = "wss://ws-sgp.tensorcharts.com/tensorWS"
CHANNEL      = "bitfinexBTCUSD"
TARGET_COUNT = 500
OUTFILE      = "sample_candles.json"

# Run the stream
asyncio.run(stream_candles(URI, CHANNEL, TARGET_COUNT, OUTFILE))
