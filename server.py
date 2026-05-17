# -*- coding: utf-8 -*-
from flask import Flask, jsonify
from flask_cors import CORS
import akshare as ak
import pandas as pd
import logging
import os
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

@app.route('/')
def home():
    return "Stock API running with akshare"

@app.route('/kline/<code>')
def get_kline(code):
    """
    获取日K线数据，返回格式与原 mootdx 版本完全一致
    """
    try:
        # 转换代码格式：sh600036 -> 600036，sz000001 -> 000001
        symbol = code.replace('sh', '').replace('sz', '')
        # 判断市场：上海是1，深圳是0（仅用于兼容原格式，这里不使用）
        
        # 使用 akshare 获取历史行情
        # 获取从2020年至今的日线数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=365*3)).strftime('%Y%m%d')
        
        # 注意：akshare 的 stock_zh_a_hist 需要股票代码和周期
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", 
                                 start_date=start_date, end_date=end_date, adjust="")
        if df is None or df.empty:
            return jsonify({'error': 'No data'}), 404
        
        # 重命名列以便处理
        df.rename(columns={
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount'
        }, inplace=True)
        
        # 转换为与原格式一致的列表
        minutes = []
        last_close = 0.0
        for _, row in df.iterrows():
            date_str = row['date'].split('-')
            time = f"{date_str[1]}-{date_str[2]}"  # MM-DD
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            volume = int(row['volume'])
            amount = float(row['amount']) if 'amount' in row else price * volume * 100
            avg_price = amount / (volume * 100) if volume > 0 else price
            
            minutes.append({
                'time': time,
                'price': price,
                'avgPrice': avg_price,
                'lastClose': last_close,
                'volume': volume,
                'amount': amount,
                'high': high,
                'low': low
            })
            last_close = price
        
        # 获取股票名称（使用 ak.stock_individual_info_em）
        name = code
        try:
            info = ak.stock_individual_info_em(symbol=symbol)
            if info is not None and not info.empty:
                name = info[info['item'] == '股票简称']['value'].values[0]
        except:
            pass
        
        return jsonify({'name': name, 'minutes': minutes})
    except Exception as e:
        logging.error(f"Kline error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<code>')
def get_quote(code):
    """
    获取实时行情，返回与原 mootdx 版本完全一致的数组格式
    """
    try:
        symbol = code.replace('sh', '').replace('sz', '')
        # 获取实时行情
        df = ak.stock_zh_a_spot_em()
        stock_info = df[df['代码'] == symbol]
        if stock_info.empty:
            return jsonify({'error': 'No quote'}), 404
        
        row = stock_info.iloc[0]
        latest = float(row['最新价'])
        last_close = float(row['昨收'])
        change_percent = float(row['涨跌幅'])
        high = float(row['最高'])
        low = float(row['最低'])
        turnover = float(row['换手率']) if '换手率' in row else 0.0
        amplitude = (high - low) / last_close * 100 if last_close != 0 else 0.0
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
            "0.00",
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
