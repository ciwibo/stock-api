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

def detect_market(code: str) -> str:
    """
    根据股票代码识别所属市场
    返回: 'a' (A股), 'hk' (港股), 'us' (美股)
    """
    code_upper = code.upper()
    # A股规则：sh/sz/bj开头，或者6位纯数字
    if (code_upper.startswith('SH') or code_upper.startswith('SZ') or
        code_upper.startswith('BJ') or (len(code_upper) == 6 and code_upper.isdigit())):
        return 'a'
    # 港股规则：以.HK结尾 或 5位数字
    if code_upper.endswith('.HK') or (len(code_upper) == 5 and code_upper.isdigit()):
        return 'hk'
    # 其余默认为美股
    return 'us'

def convert_to_yfinance_code(code: str, market: str) -> str:
    """将原始代码转换为 yfinance 接受的格式"""
    if market == 'hk':
        # 港股需要添加 .HK 后缀，并去除可能的前缀
        raw = code.upper().replace('.HK', '')
        # 如果是纯数字，补齐前导零？yfinance 港股代码通常是 5 位数字，例如 00700 -> 0700.HK
        # 这里简单处理，直接加 .HK
        return f"{raw}.HK"
    elif market == 'us':
        return code.upper()
    else:
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
            # 提取纯数字代码
            symbol = code.replace('sh', '').replace('sz', '').replace('bj', '')
            # 注意：ak.stock_zh_a_hist_min_em 参数 period='1' 表示1分钟
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, period='1', adjust='')
            if df is None or df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
            # 取最近240条
            df = df.tail(240).reset_index(drop=True)
            # 列名: '时间', '开盘', '收盘', '最高', '最低', '成交量', '成交额'
            df['time'] = pd.to_datetime(df['时间']).dt.strftime('%H:%M')
            df.rename(columns={
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount'
            }, inplace=True)
            # 确保 amount 存在，若没有则估算
            if 'amount' not in df.columns:
                df['amount'] = df['close'] * df['volume']
        else:
            # 港股/美股：使用 yfinance 获取1分钟K线
            yf_code = convert_to_yfinance_code(code, market)
            # 下载数据，period='7d', interval='1m'
            df = yf.download(yf_code, period='7d', interval='1m', progress=False)
            if df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
            # 重置索引，使 Datetime 成为列
            df = df.reset_index()
            # 取最近120条（yfinance 1分钟数据最多返回约7天*390分钟，足够）
            df = df.tail(120).reset_index(drop=True)
            df['time'] = pd.to_datetime(df['Datetime']).dt.strftime('%H:%M')
            # 重命名列
            df.rename(columns={
                'Close': 'close',
                'High': 'high',
                'Low': 'low',
                'Volume': 'volume'
            }, inplace=True)
            # 估算成交额
            df['amount'] = df['close'] * df['volume']

        # 构建返回数据
        minutes = []
        last_close = 0.0
        total_amount = 0.0
        total_volume = 0

        for _, row in df.iterrows():
            # 确保每个值是标量（float）
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            volume = int(row['volume'])
            amount = float(row['amount'])
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

        # 获取股票名称（简单处理，A股从代码提取）
        if market == 'a':
            # 尝试从 akshare 获取名称
            try:
                spot_df = ak.stock_zh_a_spot_em()
                symbol_num = code.replace('sh', '').replace('sz', '').replace('bj', '')
                name_row = spot_df[spot_df['代码'] == symbol_num]
                if not name_row.empty:
                    name = name_row.iloc[0]['名称']
                else:
                    name = code
            except:
                name = code
        else:
            yf_code = convert_to_yfinance_code(code, market)
            name = get_stock_name_yfinance(yf_code)

        return jsonify({'name': name, 'minutes': minutes})

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
            # A股：使用 akshare 获取全量实时行情
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
            # 计算振幅
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = float(row['市盈率-动态']) if '市盈率-动态' in row else 0.0
            market_cap = float(row['总市值']) if '总市值' in row else 0.0

        else:
            # 港股/美股：使用 yfinance
            yf_code = convert_to_yfinance_code(code, market)
            ticker = yf.Ticker(yf_code)
            info = ticker.info

            # 获取最新价
            latest = info.get('regularMarketPrice')
            if latest is None:
                # 尝试从历史数据获取最后一分钟
                hist = ticker.history(period='1d', interval='1m')
                if not hist.empty:
                    latest = hist['Close'].iloc[-1]
                else:
                    latest = 0.0
            else:
                latest = float(latest)

            last_close = info.get('regularMarketPreviousClose')
            if last_close is None:
                last_close = latest
            else:
                last_close = float(last_close)

            change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0.0
            high = info.get('regularMarketDayHigh')
            if high is None:
                high = latest
            else:
                high = float(high)
            low = info.get('regularMarketDayLow')
            if low is None:
                low = latest
            else:
                low = float(low)
            turnover = 0.0
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = info.get('trailingPE', 0.0)
            if pe is None:
                pe = 0.0
            else:
                pe = float(pe)
            market_cap = info.get('marketCap', 0.0)
            if market_cap is None:
                market_cap = 0.0
            else:
                market_cap = float(market_cap)

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
    return "A股/港股/美股数据服务运行中 (A股:akshare, 港股美股:yfinance)"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
