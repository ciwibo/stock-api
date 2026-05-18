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

# ---------- 辅助函数 ----------
def detect_market(code: str) -> str:
    """
    根据股票代码识别所属市场
    返回: 'a' (A股), 'hk' (港股), 'us' (美股)
    """
    code_upper = code.upper()
    # A股规则：sh/sz/bj 开头，或者纯6位数字
    if (code_upper.startswith('SH') or code_upper.startswith('SZ') or
        code_upper.startswith('BJ') or (len(code_upper) == 6 and code_upper.isdigit())):
        return 'a'
    # 港股规则：带.HK后缀或5位数字（港股代码通常5位）
    if code_upper.endswith('.HK') or (len(code_upper) == 5 and code_upper.isdigit()):
        return 'hk'
    # 其余默认为美股
    return 'us'

def convert_to_yfinance_code(code: str, market: str) -> str:
    """将原始代码转换为 yfinance 接受的格式"""
    if market == 'hk':
        # 港股需要添加 .HK 后缀，并去除可能已有的 .HK
        raw = code.upper().replace('.HK', '')
        return f"{raw}.HK"
    elif market == 'us':
        # 美股直接使用原代码
        return code.upper()
    else:
        return code  # A股不会用到

# ---------- K线接口（1分钟分时） ----------
@app.route('/kline/<code>')
def get_kline(code):
    """
    返回1分钟K线数据，格式匹配 Android 的 MinuteData
    """
    market = detect_market(code)
    logging.info(f"K线请求 - 代码: {code} -> 市场: {market}")

    try:
        if market == 'a':
            # A股：使用 akshare 获取1分钟K线
            # 提取纯数字代码
            symbol = code.replace('sh', '').replace('sz', '').replace('bj', '')
            # 使用 akshare 的最新分钟数据接口
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, period='1', adjust='')
            if df is None or df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
            
            # 保留最近240条
            df = df.tail(240).reset_index(drop=True)
            # 时间列格式化
            df['时间'] = pd.to_datetime(df['时间'])
            df['time'] = df['时间'].dt.strftime('%H:%M')
            # 重命名列
            df.rename(columns={'收盘': 'close', '最高': 'high', '最低': 'low', '成交量': 'volume'}, inplace=True)

        else:
            # 港股/美股：使用 yfinance 获取1分钟K线
            yf_code = convert_to_yfinance_code(code, market)
            logging.info(f"yfinance 代码: {yf_code}")
            # 下载最近7天的1分钟数据
            df = yf.download(yf_code, period='7d', interval='1m', progress=False)
            if df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
            
            # 重置索引，把 Datetime 变成列
            df = df.reset_index()
            # 确保列名小写
            df.columns = [col.lower() for col in df.columns]
            # 时间格式化
            df['time'] = pd.to_datetime(df['datetime']).dt.strftime('%H:%M')
            # 只取最近120条（yfinance 1分钟数据量有限）
            df = df.tail(120)
            # 重命名列（如果需要）
            if 'close' not in df.columns:
                df.rename(columns={'close': 'close', 'high': 'high', 'low': 'low', 'volume': 'volume'}, inplace=True)

        # 构建返回数据
        minutes = []
        last_close = 0.0
        total_amount = 0.0
        total_volume = 0

        for _, row in df.iterrows():
            # 安全获取数值：使用 .item() 或直接 float() 确保是标量
            try:
                price = float(row['close'])
                high = float(row['high'])
                low = float(row['low'])
                volume = int(row['volume'])
            except Exception as e:
                logging.error(f"数据转换错误: {e}, row type: {type(row['close'])}")
                continue

            # 成交额估算
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

        # 获取股票名称（简化，A股可从 akshare 获取，美股港股从 yfinance）
        name = code
        if market == 'a':
            try:
                # 从实时行情获取名称（简单方法）
                spot = ak.stock_zh_a_spot_em()
                row = spot[spot['代码'] == symbol]
                if not row.empty:
                    name = row.iloc[0]['名称']
            except:
                pass
        else:
            try:
                yf_code = convert_to_yfinance_code(code, market)
                ticker = yf.Ticker(yf_code)
                info = ticker.info
                name = info.get('longName') or info.get('shortName') or code
            except:
                pass

        return jsonify({'name': name, 'minutes': minutes})

    except Exception as e:
        logging.error(f"K线接口错误: {e}")
        return jsonify({'error': str(e)}), 500


# ---------- 实时行情接口 ----------
@app.route('/quote/<code>')
def get_quote(code):
    """
    返回10个元素的数组，顺序：
    最新价, 昨收, 涨跌幅%, 换手率%, 量比, 振幅%, 最高, 最低, 市盈率, 总市值
    """
    market = detect_market(code)
    logging.info(f"实时行情请求 - 代码: {code} -> 市场: {market}")

    try:
        if market == 'a':
            # A股：使用 akshare 的实时行情接口
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
            amplitude = float(row['振幅']) if '振幅' in row else 0.0
            pe = float(row['市盈率-动态']) if '市盈率-动态' in row else 0.0
            market_cap = float(row['总市值']) if '总市值' in row else 0.0

        else:
            # 港股/美股：使用 yfinance
            yf_code = convert_to_yfinance_code(code, market)
            ticker = yf.Ticker(yf_code)
            info = ticker.info

            latest = info.get('regularMarketPrice', 0.0)
            if latest == 0.0:
                latest = info.get('currentPrice', 0.0)
            last_close = info.get('regularMarketPreviousClose', latest)
            if last_close == 0.0:
                last_close = info.get('previousClose', latest)
            change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0.0
            high = info.get('regularMarketDayHigh', latest)
            low = info.get('regularMarketDayLow', latest)
            turnover = 0.0  # yfinance 不直接提供
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = info.get('trailingPE', 0.0) or info.get('forwardPE', 0.0)
            market_cap = info.get('marketCap', 0.0)

        # 市值格式化
        def fmt_market_cap(v):
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
            "0.00",   # 量比占位
            f"{amplitude:.2f}",
            f"{high:.2f}",
            f"{low:.2f}",
            f"{pe:.2f}" if pe > 0 else "---",
            fmt_market_cap(market_cap)
        ]
        return jsonify(result)

    except Exception as e:
        logging.error(f"实时行情接口错误: {e}")
        return jsonify({'error': str(e)}), 500


# ---------- 根路由 ----------
@app.route('/')
def home():
    return "A股/港股/美股数据服务运行中 (A股:akshare+新浪, 港股美股:yfinance)"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
