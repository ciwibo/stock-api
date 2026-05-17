# -*- coding: utf-8 -*-
import os
import logging
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

def convert_code(code):
    """
    将 Android 端传入的代码转换为 yfinance 接受的格式
    - sh600036 -> 600036.SS (A股上海)
    - sz000001 -> 000001.SZ (A股深圳)
    - 纯数字且6开头 -> 数字.SS
    - 纯数字且0/3开头 -> 数字.SZ
    - 纯字母（如 AAPL） -> 原样大写
    - 带后缀的（如 0700.HK） -> 原样
    """
    code_lower = code.lower()
    # 已经是标准格式的某些股票（如 AAPL）直接返回大写
    if code_lower.isalpha():
        return code.upper()
    # 处理 sh / sz 前缀
    if code_lower.startswith('sh'):
        return f"{code[2:]}.SS"
    if code_lower.startswith('sz'):
        return f"{code[2:]}.SZ"
    # 纯数字 A 股
    if code_lower.isdigit() and len(code_lower) == 6:
        if code_lower[0] == '6':
            return f"{code}.SS"
        else:
            return f"{code}.SZ"
    # 其他情况原样返回（例如 0700.HK）
    return code.upper()

def get_stock_name(ticker_symbol):
    """获取股票名称"""
    try:
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
    返回格式完全匹配 Android 端 MinuteData
    """
    try:
        yf_code = convert_code(code)
        logging.info(f"请求1分钟K线: {code} -> {yf_code}")
        
        # yfinance 获取1分钟数据需要指定 period 和 interval
        # 为了获取足够的1分钟数据，选择 period="7d" 最多可获取7天
        ticker = yf.Ticker(yf_code)
        df = ticker.history(period="7d", interval="1m")
        
        if df is None or df.empty:
            return jsonify({'error': f'No 1min kline data for {code}'}), 404
        
        # 重置索引，将 datetime 变为列
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
        total_amount = 0.0   # 累计成交额（元或美元）
        total_volume = 0      # 累计成交量（股）
        
        for _, row in df.iterrows():
            dt = row['datetime']
            # 时间格式化为 "HH:MM"
            time_str = dt.strftime('%H:%M')
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            volume = int(row['volume'])           # 股
            # 成交额估算：收盘价 * 成交量（yfinance 不提供成交额）
            amount = price * volume
            # 累计成交额和成交量（用于计算均价）
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
        
        # 获取股票名称
        name = get_stock_name(yf_code)
        
        return jsonify({'name': name, 'minutes': minutes})
    
    except Exception as e:
        logging.error(f"Kline error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<code>')
def get_quote(code):
    """
    获取实时行情，返回10元素数组，顺序：
    [最新价, 昨收, 涨跌幅%, 换手率%, 量比, 振幅%, 最高, 最低, 市盈率, 总市值]
    """
    try:
        yf_code = convert_code(code)
        logging.info(f"请求实时行情: {code} -> {yf_code}")
        
        ticker = yf.Ticker(yf_code)
        info = ticker.info
        
        # 获取最新价
        latest = info.get('regularMarketPrice') or info.get('currentPrice')
        if latest is None:
            # 尝试获取最近一分钟的数据作为当前价
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                latest = hist['Close'].iloc[-1]
            else:
                latest = 0.0
        else:
            latest = float(latest)
        
        # 昨收
        last_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
        if last_close is None:
            last_close = latest
        else:
            last_close = float(last_close)
        
        # 涨跌幅
        if last_close != 0:
            change_percent = (latest - last_close) / last_close * 100
        else:
            change_percent = 0.0
        
        # 最高、最低
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
        
        # 振幅
        amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
        
        # 换手率（yfinance 通常没有）
        turnover = 0.0
        # 量比（yfinance 没有）
        volume_ratio = 0.0
        # 市盈率
        pe = info.get('trailingPE') or info.get('forwardPE') or 0
        if pe is not None:
            pe = float(pe)
        else:
            pe = 0.0
        # 总市值
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
            f"{latest:.2f}",
            f"{last_close:.2f}",
            f"{change_percent:.2f}",
            f"{turnover:.2f}",
            f"{volume_ratio:.2f}",
            f"{amplitude:.2f}",
            f"{high:.2f}",
            f"{low:.2f}",
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
