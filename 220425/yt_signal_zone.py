import cv2
import yt_dlp
import easyocr
import numpy as np
import time
import redis
import json
import threading
import torch
import warnings
import sys
import re

warnings.filterwarnings("ignore", message="RNN module weights are not part of single contiguous chunk")

r = redis.Redis(host='localhost', port=6379, db=0)
BTC_URL = "https://www.youtube.com/live/jkP1Sw7M2iU"
reader = easyocr.Reader(['en'])

SUPPLY_HSV = ((54, 78, 38), (66, 174, 82))
DEMAND_HSV = ((2, 135, 47), (12, 252, 130))
KERNEL = np.ones((5, 5), np.uint8)

def flatten_rnn(ocr_reader):
    if not hasattr(ocr_reader, "model"):
        return
    for module in ocr_reader.model.modules():
        if isinstance(module, (torch.nn.RNN, torch.nn.LSTM, torch.nn.GRU)):
            module.flatten_parameters()

def parse_trading_signal(text):
    lower_text = text.lower()
    for key in ["take profit", "take", "tp"]:
        if key in lower_text:
            return "Take Profit"
    for key in ["buy signal", "long signal", "long singal", "buy/long", "buy", "long"]:
        if key in lower_text:
            return "Buy Signal"
    for key in ["sell/short", "sell signal", "short signal", "short singal", "sell", "short"]:
        if key in lower_text:
            return "Short Signal"
    return None

def parse_price_label(text):
    clean = text.replace(',', '').replace('$', '').strip().lower()
    if clean.endswith('k'):
        try:
            return float(clean[:-1]) * 1000
        except ValueError:
            return None
    try:
        return float(clean)
    except ValueError:
        return None

def is_valid_btc_price(p):
    return 10000 < p < 100000

def get_closest_price(y_coord, price_list):
    if not price_list:
        return ""
    closest = min(price_list, key=lambda p: abs(p[0] - y_coord))
    return closest[1]

def detect_zone_bounds(hsv_img, lower_hsv, upper_hsv):
    mask = cv2.inRange(hsv_img, np.array(lower_hsv), np.array(upper_hsv))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, KERNEL)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, KERNEL)
    ys = np.where(cleaned > 0)[0]
    return (int(np.min(ys)), int(np.max(ys))) if len(ys) > 0 else (None, None)

class YouTubeStream:
    def __init__(self, url):
        self.url = url
        self.cap = None

    def connect(self):
        ydl_opts = {'format': 'best[ext=mp4]/bestvideo+bestaudio/best'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
            direct_url = info["url"]
        self.cap = cv2.VideoCapture(direct_url)

    def read_frame(self):
        if not self.cap or not self.cap.isOpened():
            self.connect()
        return self.cap.read()

    def release(self):
        if self.cap:
            self.cap.release()

def stream_worker(url, symbol):
    current_signal_type = None
    last_known_signal = {"text": "", "price": "", "coordinates": ""}

    while True:
        try:
            stream = YouTubeStream(url)
            stream.connect()
            retry_count = 0

            while True:
                ret, frame = stream.read_frame()
                if not ret or frame is None:
                    retry_count += 1
                    if retry_count >= 5:
                        break
                    time.sleep(5)
                    continue
                retry_count = 0

                h, w = frame.shape[:2]
                x0 = int(w * 0.70)
                roi = frame[:, x0:]
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                flatten_rnn(reader)

                # OCR for signals
                ocr_results = reader.readtext(gray)
                y_min_s, y_max_s = detect_zone_bounds(hsv, *SUPPLY_HSV)
                y_min_d, y_max_d = detect_zone_bounds(hsv, *DEMAND_HSV)

                # OCR for price scale
                scale_bar_region = frame[:, int(w * 0.90):]
                scale_gray = cv2.cvtColor(scale_bar_region, cv2.COLOR_BGR2GRAY)
                sharp = cv2.addWeighted(scale_gray, 1.5, cv2.GaussianBlur(scale_gray, (3, 3), 0), -0.5, 0)
                _, thresh = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                ocr_price_data = reader.readtext(thresh)
                price_map = []
                for (bbox, text, _) in ocr_price_data:
                    y = int(bbox[0][1])
                    val = parse_price_label(text)
                    if val and is_valid_btc_price(val):
                        price_map.append((y, val))

                # Build zones
                supply_zone = {"min": "", "max": ""}
                if y_min_s is not None and y_max_s is not None and price_map:
                    min_p = get_closest_price(y_max_s, price_map)
                    max_p = get_closest_price(y_min_s, price_map)
                    if min_p and max_p:
                        supply_zone = {"min": str(min_p), "max": str(max_p)}

                demand_zone = {"min": "", "max": ""}
                if y_min_d is not None and y_max_d is not None and price_map:
                    min_p = get_closest_price(y_max_d, price_map)
                    max_p = get_closest_price(y_min_d, price_map)
                    if min_p and max_p:
                        demand_zone = {"min": str(min_p), "max": str(max_p)}

                # Detect latest signal
                all_signals = []
                for bbox, txt, _ in ocr_results:
                    tl, _, _, _ = bbox
                    abs_x = int(tl[0] + x0)
                    abs_y = int(tl[1])
                    fixed = parse_trading_signal(txt.strip())
                    if fixed:
                        all_signals.append({"x": abs_x, "y": abs_y, "text": fixed})

                valid_position = None
                if all_signals:
                    all_signals.sort(key=lambda s: s["x"], reverse=True)
                    sig = all_signals[0]
                    current_signal_type = sig["text"]
                    last_known_signal = {
                        "text": current_signal_type,
                        "price": "",
                        "coordinates": f"{sig['x']},{sig['y']}"
                    }
                    dist_to_supply = abs(sig["y"] - y_min_s) if y_min_s is not None else float('inf')
                    dist_to_demand = abs(sig["y"] - y_max_d) if y_max_d is not None else float('inf')
                    if current_signal_type == "Buy Signal":
                        valid_position = dist_to_demand < dist_to_supply
                    elif current_signal_type == "Short Signal":
                        valid_position = dist_to_supply < dist_to_demand

                # **CAST** numpy.bool_ → native bool (leave None as is)
                if valid_position is not None:
                    valid_position = bool(valid_position)

                aggregated = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "last_signal": last_known_signal,
                    "supply_zone": supply_zone,
                    "demand_zone": demand_zone,
                    "valid_position": valid_position
                }

                # Compare with last pushed signal text
                try:
                    raw = r.lindex(f"{symbol}_signal", -1)
                    last_text = json.loads(raw).get("last_signal", {}).get("text", "") if raw else ""
                except Exception:
                    last_text = ""

                # Only push on change
                if aggregated["last_signal"]["text"] != last_text:
                    try:
                        r.rpush(f"{symbol}_signal", json.dumps(aggregated))
                        print(f"[{symbol}] →", aggregated)
                    except Exception as e:
                        print(f"Redis write error for {symbol}:", e)

                time.sleep(10)

            stream.release()
            time.sleep(5)

        except Exception:
            time.sleep(5)
            if 'stream' in locals():
                stream.release()

def run_streams():
    btc_thread = threading.Thread(
        target=stream_worker,
        args=(BTC_URL, "BTCUSDT"),
        name="YouTubeOCR_BTC",
        daemon=True
    )
    btc_thread.start()
    btc_thread.join()

if __name__ == "__main__":
    try:
        run_streams()
    except KeyboardInterrupt:
        sys.exit(0)
