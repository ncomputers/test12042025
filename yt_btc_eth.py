import cv2
import yt_dlp
import easyocr
import numpy as np
import time
import redis
import json
from difflib import SequenceMatcher
import threading
import torch  # Needed to check and call flatten_parameters()

# Redis connection
r = redis.Redis(host='localhost', port=6379, db=0)

# YouTube URLs for BTC and ETH
BTC_URL = "https://www.youtube.com/live/jkP1Sw7M2iU"
ETH_URL = "https://www.youtube.com/live/9M93_6S9TaQ"

# OCR reader
reader = easyocr.Reader(['en'])

def flatten_rnn(ocr_reader):
    """
    If the provided EasyOCR reader has a 'model' attribute, iterate over its submodules
    and call flatten_parameters() on any RNN, LSTM, or GRU modules.
    If no 'model' attribute exists, simply do nothing.
    """
    if not hasattr(ocr_reader, "model"):
        return
    for module in ocr_reader.model.modules():
        if isinstance(module, (torch.nn.RNN, torch.nn.LSTM, torch.nn.GRU)):
            module.flatten_parameters()

class YouTubeStream:
    def __init__(self, url):
        self.url = url
        self.cap = None

    def connect(self):
        ydl_opts = {'format': 'best[ext=mp4]/bestvideo+bestaudio/best'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(self.url, download=False)
            direct_url = info_dict["url"]
        self.cap = cv2.VideoCapture(direct_url)

    def read_frame(self):
        if not self.cap or not self.cap.isOpened():
            self.connect()
        ret, frame = self.cap.read()
        return ret, frame

    def release(self):
        if self.cap:
            self.cap.release()

def parse_trading_signal(text):
    """
    Checks if the provided text contains any of the fixed keywords.
    Returns one of the following fixed texts:
      - "Take Profit" if any of ["take profit", "take", "tp"] is found,
      - "Buy Signal" if any of ["buy signal", "long signal", "long singal", "buy/long", "buy", "long"] is found,
      - "Short Signal" if any of ["sell/short", "sell signal", "short signal", "short singal", "sell", "short"] is found,
    Otherwise returns None.
    """
    lower_text = text.lower()
    
    # Check for take profit keywords first.
    tp_keywords = ["take profit", "take", "tp"]
    for key in tp_keywords:
        if key in lower_text:
            return "Take Profit"
    
    # Check for buy-related keywords.
    buy_keywords = ["buy signal", "long signal", "long singal", "buy/long", "buy", "long"]
    for key in buy_keywords:
        if key in lower_text:
            return "Buy Signal"
    
    # Check for short-related keywords.
    short_keywords = ["sell/short", "sell signal", "short signal", "short singal", "sell", "short"]
    for key in short_keywords:
        if key in lower_text:
            return "Short Signal"
    
    return None

def fuzzy_match(text, keyword, threshold=0.7):
    return SequenceMatcher(None, text.lower(), keyword.lower()).ratio() >= threshold

SUPPLY_ZONE_KEYWORDS = ["supply zone", "sup zone", "suply zone", "supply zo", "sup zo"]
DEMAND_ZONE_KEYWORDS = ["demand zone", "dem zone", "d zone", "dem zo", "dmd zone"]

def stream_worker(url, symbol):
    """
    Processes a YouTube stream to extract trading signals.
    Each time a trading signal is detected, the OCR output is converted to one of three fixed texts:
    "Buy Signal", "Take Profit", or "Short Signal". An aggregated JSON record is built in a fixed format.
    Before updating Redis, the code fetches the last stored record for that symbol and only appends
    the new record if the fixed signal text has changed.
    """
    prev_aggregated = None
    first_signal_set = False
    last_known_signal = {"text": "", "price": "", "coordinates": ""}
    
    while True:
        try:
            stream = YouTubeStream(url)
            stream.connect()
            print(f"Connected to stream for {symbol}.")
            retry_count = 0
            
            while True:
                ret, frame = stream.read_frame()
                if not ret or frame is None:
                    retry_count += 1
                    if retry_count >= 5:
                        print(f"Stream error encountered for {symbol}. Restarting stream...")
                        break
                    time.sleep(5)
                    continue
                else:
                    retry_count = 0
                
                # Crop the rightmost 25% of the frame.
                height, width = frame.shape[:2]
                roi_start = int(width * 0.75)
                roi = frame[:, roi_start:width]
                
                # Convert ROI to grayscale.
                gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                
                # Optimize PyTorch memory usage for OCR by flattening parameters if possible.
                flatten_rnn(reader)
                
                # Run OCR on the cropped region.
                results = reader.readtext(gray_roi)
                
                all_signals = []
                for (bbox, text, prob) in results:
                    (tl, _, br, _) = bbox
                    tl = (int(tl[0] + roi_start), int(tl[1]))
                    lower_text = text.lower().strip()
                    
                    fixed_text = parse_trading_signal(lower_text)
                    if fixed_text:
                        all_signals.append((tl[0], tl[1], fixed_text))
                    elif any(fuzzy_match(lower_text, kw) for kw in SUPPLY_ZONE_KEYWORDS):
                        pass  # Supply zone detection disabled.
                    elif any(fuzzy_match(lower_text, kw) for kw in DEMAND_ZONE_KEYWORDS):
                        pass  # Demand zone detection disabled.
                
                last_signal_data = {"text": "", "price": "", "coordinates": ""}
                if not first_signal_set and all_signals:
                    all_signals.sort(key=lambda s: s[0], reverse=True)
                    _, _, fixed_signal = all_signals[0]
                    last_signal_data = {"text": fixed_signal, "price": "", "coordinates": ""}
                    first_signal_set = True
                elif all_signals:
                    all_signals.sort(key=lambda s: s[0], reverse=True)
                    _, _, fixed_signal = all_signals[0]
                    last_signal_data = {"text": fixed_signal, "price": "", "coordinates": ""}
                
                if last_signal_data.get("text"):
                    last_known_signal = last_signal_data
                
                supply_zone_data = {"min": "", "max": ""}
                demand_zone_data = {"min": "", "max": ""}
                
                aggregated = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "last_signal": {
                        "text": last_known_signal.get("text", ""),
                        "price": "",
                        "coordinates": ""
                    },
                    "supply_zone": supply_zone_data,
                    "demand_zone": demand_zone_data
                }
                
                # Fetch the last stored signal for this symbol from Redis.
                try:
                    last_data = r.lindex(f"{symbol}_signal", -1)
                    if last_data is not None:
                        try:
                            last_data = json.loads(last_data)
                            last_text = last_data.get("last_signal", {}).get("text", "")
                        except Exception as e:
                            print(f"Error parsing last data for {symbol}: {e}")
                            last_text = ""
                    else:
                        last_text = ""
                except Exception as e:
                    print(f"Redis read error for {symbol}: {e}")
                    last_text = ""
                
                # Only update Redis if the fixed signal text has changed.
                if aggregated["last_signal"]["text"] != last_text:
                    try:
                        r.rpush(f"{symbol}_signal", json.dumps(aggregated))
                        print(f"Updated Redis for {symbol}:", aggregated)
                        prev_aggregated = aggregated
                    except Exception as e:
                        print(f"Redis update error for {symbol}: {e}")
                
                time.sleep(10)
            
            stream.release()
            time.sleep(5)
        
        except Exception as e:
            print(f"Exception in main loop for {symbol}: {e}")
            time.sleep(5)
            if 'stream' in locals():
                stream.release()

def run_streams():
    btc_thread = threading.Thread(target=stream_worker, args=(BTC_URL, "BTCUSDT"), name="YouTubeOCR_BTC", daemon=True)
    eth_thread = threading.Thread(target=stream_worker, args=(ETH_URL, "ETHUSDT"), name="YouTubeOCR_ETH", daemon=True)
    btc_thread.start()
    eth_thread.start()
    btc_thread.join()
    eth_thread.join()

if __name__ == "__main__":
    run_streams()
