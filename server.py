# -*- coding: utf-8 -*-
import os
import logging
import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# 全局速率控制：每次请求后休眠1秒，避免触发yfinance限流
REQUEST_DELAY = 1.0

def delay():
    time.sleep(REQUEST_DELAY)

def convert_code(code):
    """
    将 Android 端传入的代码转换为 yfinance 接受的格式
    """
    code_lower = code.lower()
    if code_lower.isalpha():
        return code.upper()
    if code_lower.startswith('sh'):
        return f"{code[2:]}.SS"
    if code_lower.startswith('sz'):
        return f"{code[2:]}.SZ"
    if code_lower.isdigit() and len(code_lower) == 6:
        if code_lower[0] == '6':
            return f"{code}.SS"
        else:
            return f"{code}.SZ"
    return code.upper()

def get_stock_name(ticker_symbol):
    """获取股票名称，带重试和延时"""
    try:
        delay()
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        name = info.get('longName') or info.get('shortName') or ticker_symbol
        return name
    except Exception as e:
        logging.error(f"获取名称失败 {ticker_symbol}: {e}")
        return ticker_symbol

@app.route('/kline/<code>')
def get_kline(code):
    """
    获取1分钟K线（分时线），返回最近240条
    """
    try:
        yf_code = convert_code(code)
        logging.info(f"请求1分钟K线: {code} -> {yf_code}")
        delay()  # 限流
        
        ticker = yf.Ticker(yf_code)
        df = ticker.history(period="7d", interval="1m")
        
        if df is None or df.empty:
            return jsonify({'error': f'No 1min kline data for {code}'}), 404
        
        df = df.reset_index()
        df.rename(columns={
            'Datetime': 'datetime',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        }, inplace=True)
        
        # 只取最近240条
        df = df.tail(240)
        
        minutes = []
        last_close = 0.0
        total_amount = 0.0
        total_volume = 0
        
        for _, row in df.iterrows():
            dt = row['datetime']
            time_str = dt.strftime('%H:%M')
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            volume = int(row['volume'])
            amount = price * volume
            total_amount += amount
            total_volume += volume
            avg_price = total_amount / total_volume if total_volume > 0 else price
            
            minutes.append({
                'time': time_str,
                'price': round(price, 2),
                'avgPrice': round(avg_price, 2),
                'lastClose': round(last_close, 2),
                'volume': volume,
                'amount': round(amount, 2),
                'high': round(high, 2),
                'low': round(low, 2)
            })
            last_close = price
        
        name = get_stock_name(yf_code)
        return jsonify({'name': name, 'minutes': minutes})
    
    except Exception as e:
        logging.error(f"Kline error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<code>')
def get_quote(code):
    """
    获取实时行情，返回10元素数组
    """
    try:
        yf_code = convert_code(code)
        logging.info(f"请求实时行情: {code} -> {yf_code}")
        delay()  # 限流
        
        ticker = yf.Ticker(yf_code)
        info = ticker.info
        
        # 如果 info 为空字典，则尝试从历史数据获取最新价
        if not info:
            logging.warning(f"info 为空，尝试从历史数据获取 {yf_code}")
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                latest = hist['Close'].iloc[-1]
                last_close = hist['Close'].iloc[0] if len(hist) > 1 else latest
                high = hist['High'].max()
                low = hist['Low'].min()
                change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0
                amplitude = (high - low) / last_close * 100 if last_close != 0 else 0
                turnover = 0.0
                volume_ratio = 0.0
                pe = 0.0
                mv = 0.0
            else:
                return jsonify({'error': f'No quote data for {code}'}), 404
        else:
            # 正常从 info 获取
            latest = info.get('regularMarketPrice') or info.get('currentPrice')
            if latest is None:
                # 回退到历史数据
                hist = ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    latest = hist['Close'].iloc[-1]
                else:
                    latest = 0.0
            else:
                latest = float(latest)
            
            last_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
            if last_close is None:
                last_close = latest
            else:
                last_close = float(last_close)
            
            change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0.0
            
            high = info.get('regularMarketDayHigh') or info.get('dayHigh')
            low = info.get('regularMarketDayLow') or info.get('dayLow')
            if high is None:
                high = latest
            else:
                high = float(high)
            if low is None:
                low = latest
            else:
                low = float(low)
            
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            turnover = 0.0
            volume_ratio = 0.0
            pe = info.get('trailingPE') or info.get('forwardPE') or 0
            if pe is not None:
                pe = float(pe)
            else:
                pe = 0.0
            mv = info.get('marketCap') or 0
            if mv is not None:
                mv = float(mv)
            else:
                mv = 0.0
        
        def fmt_mv(v):
            if v >= 1e12:
                return f"{v/1e12:.2f}万亿"
            elif v >= 1e8:
                return f"{v/1e8:.2f}亿"
            elif v == 0:
                return "---"
            else:
                return f"{v/1e4:.2f}万"
        
        result = [
            f"{latest:.2f}" if latest != 0 else "---",
            f"{last_close:.2f}" if last_close != 0 else "---",
            f"{change_percent:.2f}",
            f"{turnover:.2f}",
            f"{volume_ratio:.2f}",
            f"{amplitude:.2f}",
            f"{high:.2f}" if high != 0 else "---",
            f"{low:.2f}" if low != 0 else "---",
            f"{pe:.2f}" if pe > 0 else "---",
            fmt_mv(mv)
        ]
        return jsonify(result)
    
    except Exception as e:
        logging.error(f"Quote error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/')
def home():
    return "Stock API with yfinance (1min K-line + realtime quote) is running!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
