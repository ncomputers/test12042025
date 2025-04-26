import asyncio
import logging
from copy import deepcopy
import redis, json
from websocket_client import WebSocketClient
from config import setup_logging
from timeframe_processor import process_timeframe_without_rsi
redis_client = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)

# Market‐wide factors
from market_factors import (
    price_divergence,
    volume_accumulation_distribution,
    volatility_index,
    market_sentiment_index,
    trend_strength
)

# --- CONFIGURATION ---

URI = "wss://ws-sgp.tensorcharts.com/tensorWS"
CHANNELS = [
    "bitfinexBTCUSD",
    "binanceBTCUSDT",
    "bitstampBTCUSD",
    "deribitBTC-PERPETUAL",
    "gdaxBCH-USD"
]
TIMEFRAMES = ["15min", "5min"]

# Shared store for the latest data per channel
# data_store[ch][tf] → {"factors": {...}, "overall": float}
# data_store[ch]["extras"] → {"divergence":…, "acc_dist":…, "volatility":…, "sentiment":…, "strength":…}
# data_store[ch]["raw_vol"] → float (the 5min raw volume)
data_store = {
    ch: {tf: None for tf in TIMEFRAMES} | {"extras": None, "raw_vol": 0.0}
    for ch in CHANNELS
}


async def handle_channel(channel: str):
    """Connect to one channel, update data_store whenever a new candle arrives."""
    ws = WebSocketClient(uri=URI, channel=channel)
    logging.info(f"Subscribed to: {channel}")
    async for msg in ws.connect():
        if msg.get("type") != "candle":
            continue

        data = msg.get("data", {})
        prices = []
        volumes = []

        # process each timeframe
        for tf in TIMEFRAMES:
            candle = data.get(tf, {})
            if candle.get("close") is None:
                data_store[channel][tf] = None
                continue

            processed = process_timeframe_without_rsi(candle)
            data_store[channel][tf] = {
                "factors": processed["factors"],
                "overall": processed["overall"]
            }

            # collect for extras
            prices.append(float(candle["close"]))
            if tf == "5min":
                vol5 = float(candle.get("volume", 0.0))
                data_store[channel]["raw_vol"] = vol5
                volumes.append(vol5)

        # compute extras (divergence, acc_dist, etc.) if we have at least 2 data points
        extras = None
        if len(prices) >= 2 and len(volumes) >= 2:
            div = price_divergence(prices, volumes)
            acc = volume_accumulation_distribution(prices, volumes)
            vol_idx = volatility_index(prices, prices, prices)  # simplified: using close list for high/low
            # sentiment & strength based on 15min overall
            t15 = data_store[channel]["15min"]
            sent = strg = 0.0
            if t15:
                sent = market_sentiment_index(t15["overall"], div, acc)
                strg = trend_strength(t15["overall"], acc, div)

            extras = {
                "divergence": div,
                "acc_dist": acc,
                "volatility": vol_idx,
                "sentiment": sent,
                "strength": strg
            }

        data_store[channel]["extras"] = extras


async def global_aggregator():
    """Every second, aggregate across all channels and log a single GLOBAL_ALL line if anything changed."""
    last_snapshot = None
    # track previous second's vol_imb for delta
    last_vol_imb = {tf: 0.0 for tf in TIMEFRAMES}

    while True:
        await asyncio.sleep(1)

        # prepare accumulators
        agg = {
            tf: {k: [] for k in ("price","vwap","vol_imb","bid_ask","heatmap","overall")}
            for tf in TIMEFRAMES
        }
        raw_vol_sum = 0.0
        extras_acc = {k: [] for k in ("divergence","acc_dist","volatility","sentiment","strength")}

        # collect from every channel
        for ch in CHANNELS:
            for tf in TIMEFRAMES:
                rec = data_store[ch][tf]
                if rec:
                    f = rec["factors"]
                    agg[tf]["price"].append(f["price"])
                    agg[tf]["vwap"].append(f["vwap"])
                    agg[tf]["vol_imb"].append(f["volume"])
                    agg[tf]["bid_ask"].append(f["bid_ask"])
                    agg[tf]["heatmap"].append(f["heatmap"])
                    agg[tf]["overall"].append(rec["overall"])
            raw_vol_sum += data_store[ch]["raw_vol"] or 0.0

            ex = data_store[ch]["extras"]
            if ex:
                for k in extras_acc:
                    extras_acc[k].append(ex[k])

        # build this second's snapshot
        snap = {"tf": {}, "raw_vol_sum": raw_vol_sum, "extras": {}, "MultiTF": 0.0}
        # per-tf averages
        for tf in TIMEFRAMES:
            snap["tf"][tf] = {
                k: (sum(vs)/len(vs) if vs else 0.0)
                for k, vs in agg[tf].items()
            }
        # extras averages
        for k, vs in extras_acc.items():
            snap["extras"][k] = (sum(vs)/len(vs)) if vs else 0.0

        # MultiTF = average of all “overall”
        all_overalls = []
        for tf in TIMEFRAMES:
            all_overalls += agg[tf]["overall"]
        snap["MultiTF"] = (sum(all_overalls)/len(all_overalls)) if all_overalls else 0.0

        # only log when something changed
        if snap != last_snapshot:
            last_snapshot = deepcopy(snap)

            # build formatted parts
            parts = []
            for tf in TIMEFRAMES:
                f = snap["tf"][tf]
                cur_vi = f["vol_imb"]
                delta_vi = cur_vi - last_vol_imb[tf]
                last_vol_imb[tf] = cur_vi

                parts.append(
                    f"[{tf}] "
                    f"price:{f['price']:+.2f}%  "
                    f"vwap:{f['vwap']:+.2f}%  "
                    f"vol_imb:{cur_vi:+.2f}% Δ{delta_vi:+.2f}%  "
                    f"bid_ask:{f['bid_ask']:+.2f}%  "
                    f"heatmap:{f['heatmap']:+.2f}%"
                )

            e = snap["extras"]
            extras_str = (
                f"div:{e['divergence']:+.2f}%  "
                f"acc_dist:{e['acc_dist']:+.2f}  "
                f"vol:{e['volatility']:+.2f}%  "
                f"sent:{e['sentiment']:+.2f}%  "
                f"str:{e['strength']:+.2f}%"
            )
            if abs(cur_vi) > 99:
            # push the entire snapshot as a JSON string onto a Redis list named "vol_imb"
                redis_client.rpush("vol_imb", json.dumps(snap))


            logging.info(
                f"[GLOBAL_ALL]  " +
                "  |  ".join(parts) +
                f"  |  raw_vol_5min_sum:{snap['raw_vol_sum']:.4f}  "
                f"|  MultiTF:{snap['MultiTF']:+.2f}%  "
                f"|  {extras_str}"
            )


async def main():
    setup_logging()
    # spawn one task per channel + the global aggregator
    tasks = [asyncio.create_task(handle_channel(ch)) for ch in CHANNELS]
    tasks.append(asyncio.create_task(global_aggregator()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
