import asyncio
import logging
from copy import deepcopy
import redis
import json
from datetime import datetime
from websocket_client import WebSocketClient
from config import setup_logging
from timeframe_processor import process_timeframe_without_rsi
from binance_ws import BinanceWebsocket

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

redis_client = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=True)

# Shared store for the latest data per channel
data_store = {
    ch: {tf: None for tf in TIMEFRAMES} | {"extras": None, "raw_vol": 0.0}
    for ch in CHANNELS
}


async def handle_channel(channel: str):
    ws = WebSocketClient(uri=URI, channel=channel)
    logging.info(f"Subscribed to: {channel}")
    async for msg in ws.connect():
        if msg.get("type") != "candle":
            continue

        data = msg["data"]
        closes = []
        vols = []

        for tf in TIMEFRAMES:
            candle = data.get(tf, {})
            if candle.get("close") is None:
                data_store[channel][tf] = None
                continue

            proc = process_timeframe_without_rsi(candle)
            data_store[channel][tf] = {
                "factors": proc["factors"],
                "overall": proc["overall"]
            }

            closes.append(float(candle["close"]))
            if tf == "5min":
                v = float(candle.get("volume", 0.0))
                data_store[channel]["raw_vol"] = v
                vols.append(v)

        extras = None
        if len(closes) >= 2 and len(vols) >= 2:
            div = price_divergence(closes, vols)
            acc = volume_accumulation_distribution(closes, vols)
            vol_idx = volatility_index(closes, closes, closes)
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
    binance_ws = BinanceWebsocket()
    binance_ws.start()

    last_snapshot = None
    last_vol_imb = {tf: 0.0 for tf in TIMEFRAMES}

    while True:
        await asyncio.sleep(1)

        agg = {
            tf: {k: [] for k in ("price","vwap","vol_imb","bid_ask","heatmap","overall")}
            for tf in TIMEFRAMES
        }
        raw_sum = 0.0
        extras_acc = {k: [] for k in ("divergence","acc_dist","volatility","sentiment","strength")}

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
            raw_sum += data_store[ch]["raw_vol"] or 0.0
            ex = data_store[ch]["extras"]
            if ex:
                for k in extras_acc:
                    extras_acc[k].append(ex[k])

        snap = {"tf": {}, "raw_vol_sum": raw_sum, "extras": {}, "MultiTF": 0.0}
        for tf in TIMEFRAMES:
            snap["tf"][tf] = {
                k: (sum(vs)/len(vs) if vs else 0.0)
                for k, vs in agg[tf].items()
            }

        for k, vs in extras_acc.items():
            snap["extras"][k] = (sum(vs)/len(vs)) if vs else 0.0

        all_over = []
        for tf in TIMEFRAMES:
            all_over.extend(agg[tf]["overall"])
        snap["MultiTF"] = (sum(all_over)/len(all_over)) if all_over else 0.0

        if snap != last_snapshot:
            last_snapshot = deepcopy(snap)

            # grab live price
            with binance_ws._lock:
                price_ws = binance_ws.current_price or 0.0

            parts = []
            for tf in TIMEFRAMES:
                f = snap["tf"][tf]
                cur_v = f["vol_imb"]
                d_v = cur_v - last_vol_imb[tf]
                last_vol_imb[tf] = cur_v

                parts.append(
                    f"[{tf}] price:{f['price']:+.2f}% & priceWS:{price_ws:.1f}  "
                    f"vwap:{f['vwap']:+.2f}%  vol_imb:{cur_v:+.2f}%  delta_vi:{d_v:+.2f}%  "
                    f"bid_ask:{f['bid_ask']:+.2f}%  heatmap:{f['heatmap']:+.2f}%"
                )

            e = snap["extras"]
            extras_str = (
                f"div:{e['divergence']:+.2f}%  acc_dist:{e['acc_dist']:+.2f}  "
                f"volatility:{e['volatility']:+.2f}%  sent:{e['sentiment']:+.2f}%  "
                f"str:{e['strength']:+.2f}%"
            )

            log_line = (
                "[GLOBAL_ALL]  " +
                "  |  ".join(parts) +
                f"  |  raw_vol_5min_sum:{snap['raw_vol_sum']:.4f}  "
                f"|  MultiTF:{snap['MultiTF']:+.2f}%  "
                f"|  {extras_str}"
            )
            logging.info(log_line)

            # only write if any vol_imb >±95% or delta_vi >±50%
            trigger = any(
                abs(snap["tf"][tf]["vol_imb"]) > 20
                or abs((snap["tf"][tf]["vol_imb"] - (last_snapshot["tf"][tf]["vol_imb"]
                    if last_snapshot else 0))) > 50
                for tf in TIMEFRAMES
            )
            if trigger:
                ts = datetime.utcnow().isoformat()
                redis_client.rpush("vol_imb", f"{ts} {log_line}")


async def main():
    setup_logging()
    tasks = [asyncio.create_task(handle_channel(ch)) for ch in CHANNELS]
    tasks.append(asyncio.create_task(global_aggregator()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
