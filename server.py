# -*- coding: utf-8 -*-
import os
import time
import random
import logging
import requests
import akshare as ak
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

# ======================== 初始化配置 ========================
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# 市场判断函数
def get_market_type(code):
    """
    根据代码格式判断市场类型
    返回: 'a' 表示A股，'us' 表示美股
    """
    code_lower = code.lower()
    if code_lower.startswith('sh') or code_lower.startswith('sz'):
        return 'a'
    # 美股代码通常是纯字母，如 AAPL, MSFT
    # 如果代码包含数字或点号，可能需要特殊处理（如 0700.HK 港股），这里简化为字母判断
    if code_lower.isalpha():
        return 'us'
    # 默认当作美股处理，yfinance 会尝试解析
    return 'us'

def get_stock_name_from_yfinance(code):
    """通过 yfinance 获取股票名称"""
    try:
        ticker = yf.Ticker(code.upper())
        info = ticker.info
        name = info.get('longName', '')
        if name:
            return name
        name = info.get('shortName', '')
        if name:
            return name
    except Exception as e:
        logging.error(f"获取 {code} 名称失败: {e}")
    return code.upper()

# ========== K线接口 ==========
@app.route('/kline/<code>')
def get_kline(code):
    """
    获取日K线数据
    - A股: 使用 akshare (stock_zh_a_hist)
    - 美股: 使用 yfinance (history)
    """
    try:
        market = get_market_type(code)
        minutes = []
        last_close = 0.0
        
        if market == 'a':
            # A股逻辑：使用 akshare
            symbol = code.replace('sh', '').replace('sz', '')
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=365*2)).strftime('%Y%m%d')
            
            # 重试机制
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol, 
                        period="daily", 
                        start_date=start_date, 
                        end_date=end_date, 
                        adjust=""
                    )
                    if df is not None and not df.empty:
                        break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(1)
            
            if df is None or df.empty:
                return jsonify({'error': 'No A-share data'}), 404
            
            df.rename(columns={
                '日期': 'date',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount'
            }, inplace=True)
            
            for _, row in df.iterrows():
                date_str = row['date'].split('-')
                time_str = f"{date_str[1]}-{date_str[2]}"
                price = float(row['close'])
                high = float(row['high'])
                low = float(row['low'])
                volume = int(row['volume'])
                # 成交额单位是元，需转为万元（兼容安卓端显示）
                amount = float(row['amount']) / 10000 if 'amount' in row else price * volume
                avg_price = amount / (volume) if volume > 0 else price
                
                minutes.append({
                    'time': time_str,
                    'price': price,
                    'avgPrice': avg_price,
                    'lastClose': last_close,
                    'volume': volume,
                    'amount': amount,
                    'high': high,
                    'low': low
                })
                last_close = price
            
            # 获取A股名称（通过akshare）
            name = code
            try:
                name_df = ak.stock_individual_info_em(symbol=symbol)
                if name_df is not None and not name_df.empty:
                    name = name_df[name_df['item'] == '股票简称']['value'].values[0]
            except:
                pass
        
        else:
            # 美股逻辑：使用 yfinance
            ticker_symbol = code.upper()
            end_date = datetime.now()
            start_date = end_date - timedelta(days=365*2)
            
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(start=start_date, end=end_date, interval="1d")
            
            if df is None or df.empty:
                return jsonify({'error': f'No data for {ticker_symbol}'}), 404
            
            # 重置索引，将日期列从索引变成普通列
            df = df.reset_index()
            # 重命名列以统一格式
            df.rename(columns={
                'Date': 'date',
                'Close': 'close',
                'High': 'high',
                'Low': 'low',
                'Volume': 'volume'
            }, inplace=True)
            
            for _, row in df.iterrows():
                date_str = row['date'].strftime('%Y-%m-%d').split('-')
                time_str = f"{date_str[1]}-{date_str[2]}"
                price = float(row['close'])
                high = float(row['high'])
                low = float(row['low'])
                volume = int(row['volume'])
                # 成交额估算：收盘价 * 成交量
                amount = price * volume / 10000  # 转为万元
                avg_price = amount / (volume) if volume > 0 else price
                
                minutes.append({
                    'time': time_str,
                    'price': price,
                    'avgPrice': avg_price,
                    'lastClose': last_close,
                    'volume': volume,
                    'amount': amount,
                    'high': high,
                    'low': low
                })
                last_close = price
            
            # 获取美股名称
            name = get_stock_name_from_yfinance(ticker_symbol)
        
        return jsonify({'name': name, 'minutes': minutes})
    
    except Exception as e:
        logging.error(f"Kline error for {code}: {e}")
        return jsonify({'error': str(e)}), 500


# ========== 实时行情接口 ==========
@app.route('/quote/<code>')
def get_quote(code):
    """
    获取实时行情，返回长度为10的数组
    - A股: 使用 akshare 的 stock_zh_a_spot_em
    - 美股: 使用 yfinance 的 ticker.info
    """
    try:
        market = get_market_type(code)
        
        if market == 'a':
            # A股逻辑：从全量实时行情表中筛选
            # 注意：akshare 的美股实时行情 stock_us_spot_em 在云环境常失败，但对A股来说 stock_zh_a_spot_em 相对稳定
            try:
                spot_df = ak.stock_zh_a_spot_em()
                if spot_df is None or spot_df.empty:
                    return jsonify({'error': 'No A-share quote data'}), 404
                
                symbol = code.replace('sh', '').replace('sz', '')
                stock_row = spot_df[spot_df['代码'] == symbol]
                if stock_row.empty:
                    return jsonify({'error': f'No quote for {code}'}), 404
                
                row = stock_row.iloc[0]
                latest = float(row['最新价'])
                last_close = float(row['昨收'])
                change_percent = float(row['涨跌幅'])
                high = float(row['最高'])
                low = float(row['最低'])
                turnover = float(row['换手率']) if '换手率' in row else 0.0
                amplitude = float(row['振幅']) if '振幅' in row else 0.0
                pe = float(row['市盈率-动态']) if '市盈率-动态' in row else 0.0
                mv = float(row['总市值']) if '总市值' in row else 0.0
                
                def fmt_mv(v):
                    if v >= 1e12: return f"{v/1e12:.2f}万亿"
                    if v >= 1e8: return f"{v/1e8:.2f}亿"
                    return "---" if v == 0 else f"{v/1e4:.2f}万"
                
                result = [
                    f"{latest:.2f}",
                    f"{last_close:.2f}",
                    f"{change_percent:.2f}",
                    f"{turnover:.2f}",
                    "0.00",          # 量比占位
                    f"{amplitude:.2f}",
                    f"{high:.2f}",
                    f"{low:.2f}",
                    f"{pe:.2f}" if pe > 0 else "---",
                    fmt_mv(mv)
                ]
                return jsonify(result)
            except Exception as e:
                logging.error(f"A-share quote error: {e}")
                return jsonify({'error': str(e)}), 500
        
        else:
            # 美股逻辑：使用 yfinance 获取实时数据
            ticker_symbol = code.upper()
            try:
                ticker = yf.Ticker(ticker_symbol)
                # 获取实时报价
                # 方法1：使用 info
                info = ticker.info
                latest = info.get('regularMarketPrice') or info.get('currentPrice') or 0
                last_close = info.get('regularMarketPreviousClose') or info.get('previousClose') or latest
                change_percent = info.get('regularMarketChangePercent', 0)
                high = info.get('regularMarketDayHigh', latest)
                low = info.get('regularMarketDayLow', latest)
                # 方法2：获取1分钟实时数据（更精确）
                realtime = ticker.history(period="1d", interval="1m")
                if not realtime.empty:
                    latest = realtime['Close'].iloc[-1]
                    high = max(high, realtime['High'].max())
                    low = min(low, realtime['Low'].min()) if low > 0 else realtime['Low'].min()
                
                if latest == 0:
                    return jsonify({'error': f'No quote for {ticker_symbol}'}), 404
                
                # 计算振幅
                amplitude = (high - low) / last_close * 100 if last_close != 0 else 0
                # yfinance 无法直接获取市盈率和总市值，使用 info 中的数据
                pe = info.get('trailingPE', 0) or info.get('forwardPE', 0) or 0
                mv = info.get('marketCap', 0) or 0
                turnover = info.get('volume', 0) / (info.get('sharesOutstanding', 1)) * 100 if info.get('sharesOutstanding') else 0
                
                def fmt_mv(v):
                    if v >= 1e12: return f"{v/1e12:.2f}万亿"
                    if v >= 1e8: return f"{v/1e8:.2f}亿"
                    return "---" if v == 0 else f"{v/1e4:.2f}万"
                
                result = [
                    f"{latest:.2f}",
                    f"{last_close:.2f}",
                    f"{change_percent:.2f}",
                    f"{turnover:.2f}",
                    "0.00",
                    f"{amplitude:.2f}",
                    f"{high:.2f}",
                    f"{low:.2f}",
                    f"{pe:.2f}" if pe > 0 else "---",
                    fmt_mv(mv)
                ]
                return jsonify(result)
            except Exception as e:
                logging.error(f"US quote error for {code}: {e}")
                return jsonify({'error': str(e)}), 500
    
    except Exception as e:
        logging.error(f"Quote error for {code}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/')
def home():
    return "Stock API with yfinance + akshare is running!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
