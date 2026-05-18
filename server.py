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


# ---------- K线接口 ----------
@app.route('/kline/<code>')
def get_kline(code):
    """
    获取1分钟K线数据
    - A股: 使用 akshare（底层走新浪源）
    - 港股/美股: 使用 yfinance
    """
    market = detect_market(code)
    logging.info(f"K线请求 - 代码: {code} -> 市场: {market}")

    try:
        if market == 'a':
            # A股：使用 akshare 获取1分钟K线
            symbol = code.replace('sh', '').replace('sz', '').replace('bj', '')
            # 获取最近3天的1分钟数据
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
            
            # 使用 akshare 的1分钟K线接口
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol,
                period='1',
                start_date=start_date,
                end_date=end_date,
                adjust=''
            )
            if df is None or df.empty:
                # 降级到日K线
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period='daily',
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust=''
                )
                if df is None or df.empty:
                    return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404
                # 对于日K线，构造分钟数据格式
                df['时间'] = pd.to_datetime(df['日期'])
                df['time'] = df['时间'].dt.strftime('%H:%M')
                df.rename(columns={'收盘': 'close', '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'}, inplace=True)
            else:
                # 处理分钟K线数据
                df['时间'] = pd.to_datetime(df['时间'])
                df['time'] = df['时间'].dt.strftime('%H:%M')
                df.rename(columns={'收盘': 'close', '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'}, inplace=True)
            
            # 保留最近240条
            df = df.tail(240).reset_index(drop=True)
            
            # 构建返回数据
            minutes = []
            last_close = 0.0
            total_amount = 0.0
            total_volume = 0

            for _, row in df.iterrows():
                try:
                    price = float(row['close'])
                    high = float(row['high'])
                    low = float(row['low'])
                    volume = int(row['volume'])
                    amount = float(row['amount']) if 'amount' in row else price * volume
                    
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
                except Exception as e:
                    logging.error(f"处理行数据失败: {e}")
                    continue

            # 获取A股名称
            name = code
            try:
                # 从 akshare 获取实时行情获取名称
                spot_df = ak.stock_zh_a_spot_em()
                stock_row = spot_df[spot_df['代码'] == symbol]
                if not stock_row.empty:
                    name = stock_row.iloc[0]['名称']
            except:
                pass

            return jsonify({'name': name, 'minutes': minutes})

        else:
            # 港股/美股：使用 yfinance
            yf_code = convert_to_yfinance_code(code, market)
            logging.info(f"yfinance 代码: {yf_code}")

            # 下载最近3天的1分钟数据
            data_df = yf.download(yf_code, period='3d', interval='1m', progress=False)
            if data_df.empty:
                return jsonify({'error': f'无法获取 {code} 的K线数据'}), 404

            # 重置索引，将日期时间转为列
            data_df = data_df.reset_index()
            data_df.columns = [col.lower() for col in data_df.columns]

            # 保留最后120条
            data_df = data_df.tail(120)

            minutes = []
            last_close = 0.0
            total_amount = 0.0
            total_volume = 0

            for _, row in data_df.iterrows():
                try:
                    # 从 Series 中提取第一个标量值
                    price = row['close'].iloc[0] if hasattr(row['close'], 'iloc') else row['close']
                    high = row['high'].iloc[0] if hasattr(row['high'], 'iloc') else row['high']
                    low = row['low'].iloc[0] if hasattr(row['low'], 'iloc') else row['low']
                    volume = row['volume'].iloc[0] if hasattr(row['volume'], 'iloc') else row['volume']
                    
                    price = float(price)
                    high = float(high)
                    low = float(low)
                    volume = int(volume)
                    
                    amount = price * volume
                    total_amount += amount
                    total_volume += volume
                    avg_price = total_amount / total_volume if total_volume > 0 else price

                    time_str = pd.to_datetime(row['datetime']).strftime('%H:%M')

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
                except Exception as e:
                    logging.error(f"处理yfinance行数据失败: {e}")
                    continue

            # 获取股票名称
            name = code.upper()
            try:
                ticker = yf.Ticker(yf_code)
                info = ticker.info
                name = info.get('longName') or info.get('shortName') or code.upper()
            except Exception as e:
                logging.error(f"获取名称失败: {e}")

            return jsonify({'name': name, 'minutes': minutes})

    except Exception as e:
        logging.error(f"K线接口错误: {e}")
        return jsonify({'error': str(e)}), 500


# ---------- 实时行情接口 ----------
@app.route('/quote/<code>')
def get_quote(code):
    """
    获取实时行情，返回10个元素的数组，顺序：
    最新价, 昨收, 涨跌幅%, 换手率%, 量比, 振幅%, 最高, 最低, 市盈率, 总市值
    """
    market = detect_market(code)
    logging.info(f"实时行情请求 - 代码: {code} -> 市场: {market}")

    try:
        if market == 'a':
            # A股：使用 akshare（新浪源）
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
            amplitude = float(row['振幅']) if '振幅' in row else (high - low) / last_close * 100 if last_close != 0 else 0.0
            pe = float(row['市盈率-动态']) if '市盈率-动态' in row else 0.0
            market_cap = float(row['总市值']) if '总市值' in row else 0.0

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
                "0.00",
                f"{amplitude:.2f}",
                f"{high:.2f}",
                f"{low:.2f}",
                f"{pe:.2f}" if pe > 0 else "---",
                fmt_market_cap(market_cap)
            ]
            return jsonify(result)

        else:
            # 港股/美股：使用 yfinance
            yf_code = convert_to_yfinance_code(code, market)
            logging.info(f"yfinance 代码: {yf_code}")

            ticker = yf.Ticker(yf_code)
            info = ticker.info

            # 获取最新价
            latest = info.get('regularMarketPrice') or info.get('currentPrice')
            if latest is None:
                hist = ticker.history(period='1d', interval='1m')
                latest = hist['Close'].iloc[-1] if not hist.empty else 0.0
            latest = float(latest)

            # 昨收
            last_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
            last_close = float(last_close) if last_close is not None else latest

            # 最高/最低
            high = info.get('regularMarketDayHigh') or info.get('dayHigh')
            low = info.get('regularMarketDayLow') or info.get('dayLow')
            high = float(high) if high is not None else latest
            low = float(low) if low is not None else latest

            # 涨跌幅
            change_percent = (latest - last_close) / last_close * 100 if last_close != 0 else 0.0

            # 振幅
            amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0

            # 市盈率
            pe = info.get('trailingPE') or info.get('forwardPE') or 0.0
            pe = float(pe) if pe is not None else 0.0

            # 总市值
            market_cap = info.get('marketCap') or 0.0
            market_cap = float(market_cap) if market_cap is not None else 0.0

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
                "0.00",  # 换手率占位
                "0.00",  # 量比占位
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


@app.route('/')
def home():
    return "A股(akshare-新浪源) 港股/美股(yfinance) 数据服务运行中"


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
