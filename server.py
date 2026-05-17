# -*- coding: utf-8 -*-
import os
import time
import random
import logging
import requests
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify
from flask_cors import CORS
from fake_useragent import UserAgent

# ======================== 初始化配置 ========================
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# 用于动态生成请求头
ua = UserAgent(fallback='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

# ======================== 核心功能：增强型 Session ========================
def create_robust_session():
    """
    创建一个带高级伪装和重试策略的 requests.Session
    """
    session = requests.Session()

    # 1. 配置重试策略 (指数退避 + 随机抖动)
    retry_strategy = Retry(
        total=3,                     # 最大重试次数
        backoff_factor=1,            # 重试间隔基数 (1, 2, 4 秒)
        status_forcelist=[429, 500, 502, 503, 504, 520], # 需要重试的HTTP状态码
        allowed_methods=["HEAD", "GET", "OPTIONS"],      # 重试的请求方法
        respect_retry_after_header=True                  # 尊重服务端的 Retry-After 头
    )
    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=retry_strategy,
        pool_block=False
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # 2. 配置请求伪装 (重点)
    def get_headers():
        """动态生成完整的请求头，模拟真实浏览器"""
        current_ua = ua.random
        # 尝试从 UA 中解析出版本号，用于 Sec-Ch-Ua
        version = "124"
        try:
            if 'Chrome/' in current_ua:
                version = current_ua.split('Chrome/')[1].split('.')[0]
        except:
            pass

        return {
            "User-Agent": current_ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Referer": "https://quote.eastmoney.com/",
            "Sec-Ch-Ua": f'"Chromium";v="{version}", "Google Chrome";v="{version}", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "Upgrade-Insecure-Requests": "1"
        }
    
    session.headers.update(get_headers())
    
    # 3. 动态更新 (每次请求时重新生成)
    original_request = session.request
    def new_request(method, url, *args, **kwargs):
        # 在发送请求前更新一次请求头
        kwargs['headers'] = get_headers()
        # 随机延时，模拟人类行为
        time.sleep(random.uniform(0.5, 1.5))
        return original_request(method, url, *args, **kwargs)
    session.request = new_request

    return session

# 创建全局 Session，复用连接
robust_session = create_robust_session()

# ======================== API 实现 ========================
@app.route('/kline/<code>')
def get_kline(code):
    """获取日K线数据 (东方财富源)"""
    try:
        symbol = code.replace('sh', '').replace('sz', '')
        # 获取足够长时间的历史数据 (比如 3 年)
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=365*3)).strftime('%Y%m%d')
        
        # 调用 AKShare 的东方财富历史接口
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="")
        if df is None or df.empty:
            return jsonify({'error': 'No data'}), 404

        # 格式化数据
        minutes = []
        last_close = 0.0
        for _, row in df.iterrows():
            # 日期格式化
            date_str = row['日期'].split('-')
            time_str = f"{date_str[1]}-{date_str[2]}"
            price = float(row['收盘'])
            high = float(row['最高'])
            low = float(row['最低'])
            volume = int(row['成交量'])
            amount = float(row['成交额']) if '成交额' in row else price * volume * 100
            avg_price = amount / (volume * 100) if volume > 0 else price
            
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

        # 获取股票名称
        name = code
        try:
            # 为了获取名称，可以调用另一个快速接口
            name_df = ak.stock_individual_info_em(symbol=symbol)
            if name_df is not None and not name_df.empty:
                name = name_df[name_df['item'] == '股票简称']['value'].values[0]
        except Exception as e:
            logging.error(f"获取名称失败: {e}")

        return jsonify({'name': name, 'minutes': minutes})

    except Exception as e:
        logging.error(f"Kline error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/quote/<code>')
def get_quote(code):
    """获取实时行情 (东方财富源，通过 Session 调用 AKShare)"""
    try:
        # 1. 获取全量行情数据 (通过 Session 调用)
        # 注意：AKShare 内部可能默认使用自己的 session，这里通过全局 session 覆盖它的底层 http 核心
        # 原理：替换 AKShare 中 ac 使用的 session (需要 akshare >= 1.12.0)
        import akshare as ak_core
        # 尝试替换 akshare 内部使用的 session (此方法因版本而异，我们退而求其次，直接通过自定义请求获取)
        # 方案：AKShare 的实时行情底层也是请求东方财富 API，我们直接用 robust_session 获取原始数据
        
        # 获取单个股票实时行情
        # 东方财富单个股票实时行情接口： https://push2.eastmoney.com/api/qt/stock/get?secid=市场.代码
        market_code = "1" if code.startswith('sh') else "0"
        symbol_num = code.replace('sh', '').replace('sz', '')
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={market_code}.{symbol_num}&fields=f58,f43,f57,f170,f46,f44,f45,f47,f48,f49,f50,f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f116,f117,f118,f119,f120,f121,f122"
        headers = robust_session.headers
        response = robust_session.get(url, timeout=10)
        data = response.json()
        
        if data.get('data') is None:
            return jsonify({'error': 'No quote'}), 404
        
        quote_data = data['data']
        latest = float(quote_data.get('f43', 0)) / 100 if quote_data.get('f43') else 0
        last_close = float(quote_data.get('f170', 0)) / 100 if quote_data.get('f170') else 0
        if last_close == 0:
            last_close = latest
        change_percent = (latest - last_close) / last_close * 100 if last_close else 0
        high = float(quote_data.get('f44', 0)) / 100 if quote_data.get('f44') else latest
        low = float(quote_data.get('f45', 0)) / 100 if quote_data.get('f45') else latest
        # 换手率
        turnover = float(quote_data.get('f168', 0)) / 100 if quote_data.get('f168') else 0
        amplitude = (high - low) / last_close * 100 if last_close else 0
        # 市盈率
        pe = float(quote_data.get('f162', 0)) / 100 if quote_data.get('f162') else 0
        # 总市值
        mv = float(quote_data.get('f116', 0)) if quote_data.get('f116') else 0
        
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
        logging.error(f"Quote error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/')
def home():
    return "Stock API with EastMoney data source is running!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
