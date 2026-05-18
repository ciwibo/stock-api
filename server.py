# -*- coding: utf-8 -*-
import os
import logging
import sys
import pandas as pd
import numpy as np
from flask import Flask, jsonify
from flask_cors import CORS

# 延迟导入可能会引起作用域问题，但 akshare 和 yfinance 在导入时会联网，可能会卡住启动。
# 为避免启动卡死，我们改为在函数内部导入（但确保 pandas 等已导入）。
# 同时设置日志输出到 stdout
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ---------- 辅助函数 ----------
def detect_market(code: str) -> str:
    """判断市场: a(沪深京), hk, us"""
    code_upper = code.upper()
    if code_upper.startswith(('SH', 'SZ', 'BJ')) or (len(code_upper) == 6 and code_upper.isdigit()):
        return 'a'
    if code_upper.endswith('.HK') or (len(code_upper) == 5 and code_upper.isdigit()):
        return 'hk'
    return 'us'

def convert_to_yfinance_code(code: str, market: str) -> str:
    if market == 'hk':
        raw = code.upper().replace('.HK', '')
        return f"{raw}.HK"
    return code.upper()

# ---------- 健康检查 ----------
@app.route('/')
@app.route('/health')
def health():
    return jsonify({"status": "ok", "message": "Stock API is running"})

# ---------- K线接口 ----------
@app.route('/kline/<code>')
def get_kline(code):
    market = detect_market(code)
    logger.info(f"K线请求: {code} -> {market}")

    try:
        if market == 'a':
            # A股：使用 akshare
            import akshare as ak
            symbol = code.replace('sh', '').replace('sz', '').replace('bj', '')
            # 尝试获取1分钟K线
            try:
                df = ak.stock_zh_a_hist_min_em(symbol=symbol, period='1', adjust='')
                if df is None or df.empty:
                    raise ValueError("分钟数据为空")
                # 重命名列
                df.rename(columns={
                    '时间': 'datetime',
                    '收盘': 'close',
                    '最高': 'high',
                    '最低': 'low',
                    '成交量': 'volume',
                    '成交额': 'amount'
                }, inplace=True)
                df['datetime'] = pd.to_datetime(df['datetime'])
                df['time'] = df['datetime'].dt.strftime('%H:%M')
                df = df.tail(240)
                is_minute = True
            except Exception as e:
                logger.warning(f"分钟数据获取失败: {e}, 切换到日线")
                # 降级到日线
                df = ak.stock_zh_a_hist(symbol=symbol, period='daily', adjust='')
                if df is None or df.empty:
                    return jsonify({'error': f'无数据 {code}'}), 404
                df.rename(columns={
                    '日期': 'datetime',
                    '收盘': 'close',
                    '最高': 'high',
                    '最低': 'low',
                    '成交量': 'volume',
                    '成交额': 'amount'
                }, inplace=True)
                df['datetime'] = pd.to_datetime(df['datetime'])
                df['time'] = df['datetime'].dt.strftime('%H:%M')
                df = df.tail(240)
                is_minute = False

            # 构建返回数据
            minutes = []
            last_close = 0.0
            total_amount = 0.0
            total_volume = 0

            for _, row in df.iterrows():
                price = float(row['close'])
                high = float(row['high'])
                low = float(row['low'])
                volume = int(row['volume'])
                # 如果有成交额则用，否则估算
                if 'amount' in row and pd.notna(row['amount']):
                    amount = float(row['amount'])
                else:
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

            # 获取名称
            name = code
            try:
                spot = ak.stock_zh_a_spot_em()
                row = spot[spot['代码'] == symbol]
                if not row.empty:
                    name = row.iloc[0]['名称']
            except Exception as e:
                logger.error(f"获取名称失败: {e}")

            return jsonify({'name': name, 'minutes': minutes})

        else:
            # 港股/美股：使用 yfinance
            import yfinance as yf
            yf_code = convert_to_yfinance_code(code, market)
            logger.info(f"yfinance 代码: {yf_code}")

            # 下载最近3天的1分钟数据
            data = yf.download(yf_code, period='3d', interval='1m', progress=False)
            if data.empty:
                return jsonify({'error': f'无数据 {code}'}), 404

            # 重置索引，确保 Datetime 成为列
            data = data.reset_index()
            # 统一列名为小写
            data.columns = [col.lower() for col in data.columns]
            # 确保列名正确
            if 'datetime' not in data.columns:
                data.rename(columns={'index': 'datetime'}, inplace=True)
            # 取最后120条
            data = data.tail(120)

            minutes = []
            last_close = 0.0
            total_amount = 0.0
            total_volume = 0

            for _, row in data.iterrows():
                # 提取标量值（解决 Series 问题）
                price = float(row['close'].iloc[0] if hasattr(row['close'], 'iloc') else row['close'])
                high = float(row['high'].iloc[0] if hasattr(row['high'], 'iloc') else row['high'])
                low = float(row['low'].iloc[0] if hasattr(row['low'], 'iloc') else row['low'])
                volume = int(row['volume'].iloc[0] if hasattr(row['volume'], 'iloc') else row['volume'])
                amount = price * volume
                total_amount += amount
                total_volume += volume
                avg_price = total_amount / total_volume if total_volume > 0 else price
                # 时间格式化
                dt = pd.to_datetime(row['datetime'])
                time_str = dt.strftime('%H:%M')

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

            # 获取名称
            name = code.upper()
            try:
                ticker = yf.Ticker(yf_code)
                info = ticker.info
                name = info.get('longName') or info.get('shortName') or code.upper()
            except Exception as e:
                logger.error(f"获取名称失败: {e}")

            return jsonify({'name': name, 'minutes': minutes})

    except Exception as e:
        logger.error(f"K线接口异常: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ---------- 实时行情接口 ----------
@app.route('/quote/<code>')
def get_quote(code):
    market = detect_market(code)
    logger.info(f"实时行情请求: {code} -> {market}")

    try:
        if market == 'a':
            import akshare as ak
            symbol_raw = code.replace('sh', '').replace('sz', '').replace('bj', '')
            df = ak.stock_zh_a_spot_em()
            stock_data = df[df['代码'] == symbol_raw]
            if stock_data.empty:
                return jsonify({'error': f'未找到 {code}'}), 404

            row = stock_data.iloc[0]
            latest = float(row['最新价'])
            last_close = float(row['昨收'])
            change_percent = float(row['涨跌幅'])
            high = float(row['最高'])
            low = float(row['最低'])
            turnover = float(row.get('换手率', 0.0))
            amplitude = float(row.get('振幅', (high - low) / last_close * 100 if last_close != 0 else 0.0))
            pe = float(row['市盈率-动态']) if '市盈率-动态' in row else 0.0
            market_cap = float(row.get('总市值', 0.0))

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
                "0.00",  # 量比占位
                f"{amplitude:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{pe:.2f}" if pe > 0 else "---",
                fmt_mv(market_cap)
            ]
            return jsonify(result)

        else:
            import yfinance as yf
            yf_code = convert_to_yfinance_code(code, market)
            ticker = yf.Ticker(yf_code)
            info = ticker.info

            # 获取最新价
            latest = info.get('regularMarketPrice') or info.get('currentPrice')
            if latest is None:
                hist = ticker.history(period='1d', interval='1m')
                latest = hist['Close'].iloc[-1] if not hist.empty else 0.0
            latest = float(latest)

            last_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
            last_close = float(last_close) if last_close is not None else latest

            high = info.get('regularMarketDayHigh') or info.get('dayHigh')
            low = info.get('regularMarketDayLow') or info.get('dayLow')
            high = float(high) if high is not None else latest
            low = float(low) if low is not None else latest

            change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0.0
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = info.get('trailingPE') or info.get('forwardPE') or 0.0
            pe = float(pe) if pe is not None else 0.0
            market_cap = info.get('marketCap') or 0.0
            market_cap = float(market_cap) if market_cap is not None else 0.0

            def fmt_mv(v):
                if v >= 1e12:
                    return f"{v/1e12:.2f}万亿"
                elif v >= 1e8:
                    return f"{v/1e8:.2f}亿"
                return "---"

            result = [
                f"{latest:.2f}",
                f"{last_close:.2f}",
                f"{change_percent:.2f}",
                "0.00",
                "0.00",
                f"{amplitude:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{pe:.2f}" if pe > 0 else "---",
                fmt_mv(market_cap)
            ]
            return jsonify(result)

    except Exception as e:
        logger.error(f"实时行情异常: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
