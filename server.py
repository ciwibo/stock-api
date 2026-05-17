# -*- coding: utf-8 -*-
from flask import Flask, jsonify
from flask_cors import CORS
from mootdx.quotes import Quotes
import logging
import os

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# ========== 修改点：手动指定通达信服务器 ==========
# 使用国内可用的通达信行情服务器（以下IP是常用的，如果失效可以换）
TDX_HOST = "211.152.52.101"   # 通达信官方服务器之一
TDX_PORT = 7709                # 通达信行情端口

# 创建客户端时直接指定服务器地址，跳过自动测速
client = Quotes.factory(market='std', host=TDX_HOST, port=TDX_PORT)
# ===============================================

@app.route('/')
def home():
    return "Hello from Render! Your Stock API is running."

@app.route('/kline/<code>')
def get_kline(code):
    try:
        if code.startswith('sh'):
            market, symbol = 1, code[2:]
        elif code.startswith('sz'):
            market, symbol = 0, code[2:]
        else:
            market, symbol = 0, code

        df = client.bars(symbol=symbol, market=market, frequency=9, offset=0, limit=240)
        if df is None or df.empty:
            return jsonify({'error': 'No data'}), 404

        minutes = []
        last_close = 0.0
        for _, row in df.iterrows():
            date = row['date'].split(' ')[0][5:]
            price = float(row['close'])
            high = float(row['high'])
            low = float(row['low'])
            volume = int(row['volume'])
            amount = float(row['amount']) if 'amount' in row else price * volume * 100
            avg_price = amount / (volume * 100) if volume > 0 else price

            minutes.append({
                'time': date,
                'price': price,
                'avgPrice': avg_price,
                'lastClose': last_close,
                'volume': volume,
                'amount': amount,
                'high': high,
                'low': low
            })
            last_close = price

        name = code
        try:
            q = client.quotes(symbol=symbol, market=market)
            if q is not None and not q.empty:
                name = q.iloc[0].get('name', code)
        except:
            pass

        return jsonify({'name': name, 'minutes': minutes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<code>')
def get_quote(code):
    try:
        if code.startswith('sh'):
            market, symbol = 1, code[2:]
        elif code.startswith('sz'):
            market, symbol = 0, code[2:]
        else:
            market, symbol = 0, code

        df = client.quotes(symbol=symbol, market=market)
        if df is None or df.empty:
            return jsonify({'error': 'No quote'}), 404

        row = df.iloc[0]
        latest = float(row['ask_price'][0]) if 'ask_price' in row else float(row.get('close', 0))
        last_close = float(row.get('pre_close', latest))
        change_percent = (latest - last_close) / last_close * 100
        high = float(row.get('high', latest))
        low = float(row.get('low', latest))
        turnover = float(row.get('turnover_rate', 0))
        amplitude = (high - low) / last_close * 100
        pe = float(row.get('pe_ttm', 0))
        mv = float(row.get('market_cap', 0))

        def fmt_mv(v):
            if v >= 1e12: return f"{v/1e12:.2f}万亿"
            if v >= 1e8: return f"{v/1e8:.2f}亿"
            return "---" if v == 0 else f"{v/1e4:.2f}万"

        return jsonify([
            f"{latest:.2f}",
            f"{last_close:.2f}",
            f"{change_percent:.2f}",
            f"{turnover:.2f}",
            "0.00",
            f"{amplitude:.2f}",
            f"{high:.2f}",
            f"{low:.2f}",
            f"{pe:.2f}" if pe>0 else "---",
            fmt_mv(mv)
        ])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
