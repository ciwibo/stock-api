# -*- coding: utf-8 -*-
import os
import logging
import akshare as ak
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# 市场识别函数
def detect_market(code: str) -> str:
    """
    根据股票代码识别所属市场
    返回: 'a' (A股), 'hk' (港股), 'us' (美股)
    """
    code_upper = code.upper()
    # A股规则
    if (code_upper.startswith('SH') or code_upper.startswith('SZ') or
        code_upper.startswith('BJ') or (len(code_upper) == 6 and code_upper.isdigit())):
        return 'a'
    # 港股规则（带.HK后缀或5位数字）
    if code_upper.endswith('.HK') or (len(code_upper) == 5 and code_upper.isdigit()):
        return 'hk'
    # 其余默认为美股
    return 'us'

def convert_to_yfinance_code(code: str, market: str) -> str:
    """将原始代码转换为 yfinance 接受的格式"""
    if market == 'hk':
        # 港股需要添加 .HK 后缀并去除可能的前缀
        raw = code.upper().replace('.HK', '')
        return f"{raw}.HK"
    elif market == 'us':
        # 美股直接使用原代码
        return code.upper()
    else:
        # A股不应进入此分支
        return code

def get_stock_name_yfinance(yf_code: str) -> str:
    """获取美股/港股名称"""
    try:
        ticker = yf.Ticker(yf_code)
        info = ticker.info
        return info.get('longName') or info.get('shortName') or yf_code
    except Exception as e:
        logging.error(f"名称获取失败 {yf_code}: {e}")
        return yf_code

# ========== 分时K线接口 ==========
@app.route('/kline/<code>')
def get_kline(code):
    """1分钟K线数据（A股240条 / 美股港股120条）"""
    market = detect_market(code)
    logging.info(f"K线请求 - 代码: {code} -> 市场: {market}")

    try:
        if market == 'a':
            # A股：使用 akshare 获取1分钟K线
            symbol = code.replace('sh', '').replace('sz', '').replace('bj', '')
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, period='1', adjust='')
            if df is None or df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
            df = df.tail(240).reset_index(drop=True)
            df['时间'] = pd.to_datetime(df['时间'])
            df['time'] = df['时间'].dt.strftime('%H:%M')
        else:
            # 港股/美股：使用 yfinance 获取1分钟K线
            yf_code = convert_to_yfinance_code(code, market)
            df = yf.download(yf_code, period='7d', interval='1m', progress=False)
            if df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
            df = df.tail(120).reset_index()
            df['time'] = pd.to_datetime(df['Datetime']).dt.strftime('%H:%M')
            df.rename(columns={'Close': 'close', 'High': 'high', 'Low': 'low', 'Volume': 'volume'}, inplace=True)

        # 构建返回数据
        minutes = []
        last_close = 0.0
        total_amount, total_volume = 0.0, 0

        for _, row in df.iterrows():
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            volume = int(row['volume'])
            amount = price * volume
            total_amount += amount
            total_volume += volume
            avg_price = total_amount / total_volume if total_volume > 0 else price

            minutes.append({
                'time': row['time'],
                'price': round(price, 2),
                'avgPrice': round(avg_price, 2),
                'lastClose': round(last_close, 2),
                'volume': volume,
                'amount': round(amount, 2),
                'high': round(high, 2),
                'low': round(low, 2)
            })
            last_close = price

        return jsonify({'name': code, 'minutes': minutes})

    except Exception as e:
        logging.error(f"K线接口错误: {e}")
        return jsonify({'error': str(e)}), 500

# ========== 实时行情接口 ==========
@app.route('/quote/<code>')
def get_quote(code):
    """实时行情（10项数据）"""
    market = detect_market(code)
    logging.info(f"实时行情请求 - 代码: {code} -> 市场: {market}")

    try:
        if market == 'a':
            # A股：使用 akshare + 新浪源
            symbol_raw = code.replace('sh', '').replace('sz', '').replace('bj', '')
            df = ak.stock_zh_a_spot_em()
            stock_data = df[df['代码'] == symbol_raw]
            if stock_data.empty:
                return jsonify({'error': f'未找到 {code} 的行情数据'}), 404

            row = stock_data.iloc[0]
            latest = float(row['最新价'])
            last_close = float(row['昨收'])
            change_percent = float(row['涨跌幅'])
            high = float(row['最高'])
            low = float(row['最低'])
            turnover = float(row['换手率']) if '换手率' in row else 0.0
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = float(row['市盈率-动态']) if '市盈率-动态' in row else 0.0
            market_cap = float(row['总市值']) if '总市值' in row else 0.0

        else:
            # 港股/美股：使用 yfinance
            yf_code = convert_to_yfinance_code(code, market)
            ticker = yf.Ticker(yf_code)
            info = ticker.info

            latest = info.get('regularMarketPrice', 0.0)
            last_close = info.get('regularMarketPreviousClose', latest)
            change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0.0
            high = info.get('regularMarketDayHigh', latest)
            low = info.get('regularMarketDayLow', latest)
            turnover = 0.0
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = info.get('trailingPE', 0.0)
            market_cap = info.get('marketCap', 0.0)

            # 添加股票名称（可选，便于调试）
            name = get_stock_name_yfinance(yf_code)
            logging.info(f"实时行情 - {code} 名称: {name}")

        # 格式化市值显示
        def fmt_market_cap(value):
            if value >= 1e12:
                return f"{value/1e12:.2f}万亿"
            elif value >= 1e8:
                return f"{value/1e8:.2f}亿"
            elif value == 0:
                return "---"
            else:
                return f"{value/1e4:.2f}万"

        result = [
            f"{latest:.2f}",            # 最新价
            f"{last_close:.2f}",        # 昨收
            f"{change_percent:.2f}",    # 涨跌幅
            f"{turnover:.2f}",          # 换手率
            "0.00",                     # 量比（占位）
            f"{amplitude:.2f}",         # 振幅
            f"{high:.2f}",              # 最高
            f"{low:.2f}",               # 最低
            f"{pe:.2f}" if pe > 0 else "---",  # 市盈率
            fmt_market_cap(market_cap)  # 总市值
        ]
        return jsonify(result)

    except Exception as e:
        logging.error(f"实时行情接口错误: {e}")
        return jsonify({'error': str(e)}), 500

# ========== 健康检查接口 ==========
@app.route('/')
def home():
    return "A股/港股/美股数据服务运行中 (A股使用akshare+新浪，港股美股使用yfinance)"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
