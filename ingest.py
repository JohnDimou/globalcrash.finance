#!/usr/bin/env python3
"""GCI Phase 1 ingestion engine.

Pulls the v1 free data sources, computes the Global Crisis Index per the design
(docs/plans/2026-09-01-global-crisis-index-design.md): rolling 20-year percentile
ranks, directional scoring (two-sided for complacency carriers), equal weight
within pillar, fixed pillar weights (credit 25 / macro 20 / valuation 15 /
housing 10 / crypto 10 / sentiment 10 / geo 10), 4-week EMA smoothing, 13-week
velocity. Missing/failed sources degrade gracefully (pillar renormalizes).

Output: data/gci_data.json
"""
import json, io, subprocess, sys
from datetime import datetime, timezone
import pandas as pd
import numpy as np

OUT = __file__.rsplit('/', 1)[0] + '/data/gci_data.json'
UA = 'Mozilla/5.0 (GCI research pipeline; personal project)'

def http(url, timeout=60, ua=None):
    """curl subprocess. NOTE: FRED's WAF tarpits a curl TLS handshake carrying a
    browser User-Agent (UA/TLS mismatch = bot signature) but serves default curl
    fine — so no UA unless a specific host needs one (pass ua=UA)."""
    cmd = ['curl', '-sfL', '--max-time', str(timeout),
           '--retry', '2', '--retry-delay', '3']
    if ua:
        cmd += ['-A', ua]
    r = subprocess.run(cmd + [url], capture_output=True, timeout=timeout * 3 + 30)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f'curl exit {r.returncode} for {url[:80]}')
    return r.stdout

# ---------------------------------------------------------------- fetchers
def fred(series):
    """FRED keyless CSV endpoint -> pd.Series (daily/weekly/monthly native)."""
    raw = http(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}')
    df = pd.read_csv(io.BytesIO(raw), na_values='.')
    df.columns = ['date', 'v']
    s = pd.Series(df['v'].values, index=pd.to_datetime(df['date'])).dropna()
    return s

_OFR_CACHE = {}

def ofr_frame():
    """OFR FSI csv as a DataFrame (headline + category/regional sub-columns), cached."""
    if 'df' not in _OFR_CACHE:
        raw = http('https://www.financialresearch.gov/financial-stress-index/data/fsi.csv')
        df = pd.read_csv(io.BytesIO(raw))
        df.index = pd.to_datetime(df[df.columns[0]])
        _OFR_CACHE['df'] = df
    return _OFR_CACHE['df']

def ofr_col(name):
    df = ofr_frame()
    col = [c for c in df.columns if c.strip().lower() == name.lower()][0]
    return pd.to_numeric(df[col], errors='coerce').dropna()

def ecb_ciss(area):
    """ECB Data Portal New CISS (daily). area: 'US' or 'U2' (euro area)."""
    url = (f'https://data-api.ecb.europa.eu/service/data/CISS/'
           f'D.{area}.Z0Z.4F.EC.SS_CIN.IDX?format=csvdata&startPeriod=1990-01-01')
    df = pd.read_csv(io.BytesIO(http(url, timeout=90)))
    return pd.Series(pd.to_numeric(df['OBS_VALUE'], errors='coerce').values,
                     index=pd.to_datetime(df['TIME_PERIOD'])).dropna()

def cboe(symbol):
    """CBOE daily index history csv (VIX3M, SKEW ...). Returns CLOSE/last value col."""
    raw = http(f'https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv')
    df = pd.read_csv(io.BytesIO(raw))
    vcol = 'CLOSE' if 'CLOSE' in df.columns else df.columns[-1]
    return pd.Series(pd.to_numeric(df[vcol], errors='coerce').values,
                     index=pd.to_datetime(df['DATE'], format='%m/%d/%Y', errors='coerce')).dropna()

def gpr_monthly():
    """Caldara-Iacoviello geopolitical risk index (monthly xls)."""
    raw = http('https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls')
    df = pd.read_excel(io.BytesIO(raw))
    dcol = [c for c in df.columns if str(c).lower() in ('month', 'date')][0]
    vcol = [c for c in df.columns if str(c).strip().upper() == 'GPR'][0]
    return pd.Series(pd.to_numeric(df[vcol], errors='coerce').values,
                     index=pd.to_datetime(df[dcol], errors='coerce')).dropna()

def nyfed_recprob():
    """NY Fed yield-curve 12m-ahead recession probability (monthly xls, 0-1)."""
    raw = http('https://www.newyorkfed.org/medialibrary/media/research/capital_markets/allmonth.xls')
    df = pd.read_excel(io.BytesIO(raw))
    dcol = df.columns[0]
    vcol = [c for c in df.columns if 'rec_prob' in str(c).lower()][0]
    s = pd.Series(pd.to_numeric(df[vcol], errors='coerce').values,
                  index=pd.to_datetime(df[dcol], errors='coerce')).dropna()
    # rows are dated by the month PREDICTED (12m ahead); re-date to publication time
    s.index = s.index - pd.DateOffset(months=12)
    return s

def binance_funding():
    """BTC perp funding (8h) history. Binance first (back to 2019); it geo-blocks US IPs
    (GitHub runners), so fall back to Bybit (back to mid-2020), paged backwards."""
    try:
        rows, start = [], 1560000000000
        for _ in range(9):
            url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000&startTime={start}'
            page = json.loads(http(url))
            if not page:
                break
            rows += page
            start = page[-1]['fundingTime'] + 1
            if len(page) < 1000:
                break
        if len(rows) > 1000:
            return pd.Series([float(r['fundingRate']) for r in rows],
                             index=[pd.to_datetime(int(r['fundingTime']), unit='ms') for r in rows]).dropna()
    except Exception as e:                          # noqa: BLE001 — fall through to Bybit
        print(f'    binance funding unavailable ({e}); trying bybit', flush=True)
    try:
        rows, end = [], None
        for _ in range(60):
            url = ('https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=200'
                   + (f'&endTime={end}' if end else ''))
            page = json.loads(http(url)).get('result', {}).get('list', [])
            if not page:
                break
            rows += page
            end = int(page[-1]['fundingRateTimestamp']) - 1
            if len(page) < 200:
                break
        if len(rows) > 1000:
            return pd.Series([float(r['fundingRate']) for r in rows],
                             index=[pd.to_datetime(int(r['fundingRateTimestamp']), unit='ms') for r in rows]).sort_index().dropna()
    except Exception as e:                          # noqa: BLE001 — fall through to the S3 mirror
        print(f'    bybit funding unavailable ({e}); trying data.binance.vision', flush=True)
    # Binance's public S3 archive: one zip per month, not geo-fenced. calc_time,interval,last_funding_rate
    import zipfile
    vals, idx = [], []
    m = pd.Timestamp('2019-09-01')
    while m <= pd.Timestamp.utcnow().tz_localize(None).normalize():
        url = (f'https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/'
               f'BTCUSDT-fundingRate-{m:%Y-%m}.zip')
        try:
            z = zipfile.ZipFile(io.BytesIO(http(url)))
            df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
            vals += df['last_funding_rate'].astype(float).tolist()
            idx += pd.to_datetime(df['calc_time'].astype('int64'), unit='ms').tolist()
        except Exception:                           # noqa: BLE001 — a missing month is not fatal
            pass
        m += pd.DateOffset(months=1)
    return pd.Series(vals, index=idx).sort_index().dropna()

def defillama_stables():
    raw = json.loads(http('https://stablecoins.llama.fi/stablecoincharts/all'))
    idx, vals = [], []
    for row in raw:
        idx.append(pd.to_datetime(int(row['date']), unit='s'))
        tc = row.get('totalCirculating', {}) or {}
        vals.append(sum(v for v in tc.values() if isinstance(v, (int, float))))
    return pd.Series(vals, index=idx).dropna()

def defillama_tvl():
    raw = json.loads(http('https://api.llama.fi/v2/historicalChainTvl'))
    return pd.Series([r['tvl'] for r in raw],
                     index=[pd.to_datetime(int(r['date']), unit='s') for r in raw]).dropna()

def coinmetrics_mvrv():
    """Coin Metrics community data via their GitHub mirror (CapMVRVCur column)."""
    raw = http('https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv')
    df = pd.read_csv(io.BytesIO(raw), usecols=['time', 'CapMVRVCur'])
    s = pd.Series(pd.to_numeric(df['CapMVRVCur'], errors='coerce').values,
                  index=pd.to_datetime(df['time'])).dropna()
    if s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s

def epu():
    raw = http('https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv')
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.dropna(subset=['year', 'month', 'day'])
    idx = pd.to_datetime(dict(year=df['year'].astype(int),
                              month=df['month'].astype(int),
                              day=df['day'].astype(int)), errors='coerce')
    vcol = [c for c in df.columns if 'polic' in c][0]
    return pd.Series(pd.to_numeric(df[vcol], errors='coerce').values, index=idx).dropna()

def cape_multpl():
    raw = http('https://www.multpl.com/shiller-pe/table/by-month', ua=UA)
    tables = pd.read_html(io.BytesIO(raw))
    df = tables[0]
    df.columns = ['date', 'v']
    v = pd.to_numeric(df['v'].astype(str).str.extract(r'([\d.]+)')[0], errors='coerce')
    s = pd.Series(v.values, index=pd.to_datetime(df['date'], errors='coerce')).dropna()
    return s.sort_index()

# ---------------------------------------------------------------- transforms
def weekly(s):
    """Resample to Friday-weekly, forward-fill (LOCF)."""
    return s.sort_index().resample('W-FRI').last().ffill()

def roll_pct(s, window=1040, min_periods=208):
    """Rolling percentile rank of the latest value, 0-100."""
    return s.rolling(window, min_periods=min_periods).apply(
        lambda w: float((w <= w[-1]).mean() * 100), raw=True)

def two_sided(pct, lam=0.4):
    """Stress side wins; extreme calm reads ~lam*100 (complacency is a signal)."""
    return np.maximum(pct, lam * (100 - pct))

# ---------------------------------------------------------------- verified crisis chronology
# Source-verified 2026-09-02 (workflow wf_72ffbb6f): drawdowns per Yardeni bear/correction
# tables (closing basis), VIX closing peaks from FRED VIXCLS, recessions from NBER's official
# business_cycle_dates.json, systemic designations from IMF Laeven-Valencia (WP 2026/094).
# Full citations: docs/upgrade-plan-2026-09-02.md §3. Do not edit numbers without a source.
EVENTS_VERIFIED = [
 dict(key='early90s', short='Gulf War \'90', name='Early-1990s Recession / Gulf War Bear',
      peak='1990-10', window='1990-07 – 1991-03', dd=-19.9, vix=36.5, nber=True, badge='BEAR + NBER RECESSION',
      desc="Iraq's invasion of Kuwait spiked oil while the US slipped into recession; S&P 500 −19.9% in 87 days. A genuine credit crunch (NFCI 0.52)."),
 dict(key='asia97', short='Asia \'97', name='Asian Financial Crisis',
      peak='1997-10', window='1997-07 – 1998-01', dd=-10.8, vix=38.2, nber=False, badge='CORRECTION',
      desc='East Asian currency collapses culminated in the Oct 27, 1997 mini-crash: Dow −7.2%, first-ever NYSE circuit breakers.'),
 dict(key='ltcm98', short='LTCM', name='Russia Default / LTCM Crisis',
      peak='1998-10', window='1998-07 – 1998-10', dd=-19.3, vix=45.7, nber=False, badge='BEAR',
      desc="Russia's Aug 1998 default plus the LTCM collapse dislocated credit and swap markets worldwide; a NY Fed-brokered rescue stabilized markets."),
 dict(key='dotcom', short='Dot-com', name='Dot-Com Bust',
      peak='2002-10', window='2000-03 – 2002-10', dd=-49.1, vix=45.1, nber=True, badge='SEVERE BEAR + NBER RECESSION',
      desc='The tech bubble burst March 2000: S&P 500 −49.1% over 929 days, Nasdaq −77.9%. The S&P did not regain its peak until 2007.'),
 dict(key='sept11', short='9/11', name='9/11 Market Shock',
      peak='2001-09', window='2001-09 – 2001-10', dd=-11.6, vix=49.4, nber=True, badge='CORRECTION + NBER RECESSION',
      desc='The attacks closed US markets four days, the longest halt since 1933; −11.6% over the reopening week, recovered within a month.'),
 dict(key='gfc08', short='Lehman', name='Global Financial Crisis / Lehman',
      peak='2008-10', window='2007-10 – 2009-03', dd=-56.8, vix=80.9, nber=True, badge='SEVERE BEAR + NBER RECESSION + LV SYSTEMIC',
      desc='Subprime collapse and the Sep 15, 2008 Lehman bankruptcy: S&P 500 −56.8% over 517 days; VIX record close 80.86; OFR FSI all-time high 29.32. IMF-classified US systemic banking crisis 2007–2011.'),
 dict(key='flash10', short='Flash Crash', name='Flash Crash / First Greek Crisis',
      peak='2010-05', window='2010-04 – 2010-07', dd=-16.0, vix=45.8, nber=False, badge='CORRECTION',
      desc='May 6, 2010: amid Greek-default fears an algorithmic sell program erased ~$1T intraday; broader European-debt correction −16%.'),
 dict(key='euro11', short='Euro debt', name='US Downgrade / Euro Debt Crisis',
      peak='2011-08', window='2011-05 – 2011-10', dd=-19.4, vix=48.0, nber=False, badge='BEAR',
      desc="S&P's first-ever US sovereign downgrade plus Italian/Spanish debt fears: Black Monday Aug 8, 2011 saw the S&P −6.66%."),
 dict(key='china15', short='China \'15', name='China Devaluation Scare',
      peak='2015-08', window='2015-06 – 2016-02', dd=-14.2, vix=40.7, nber=False, badge='CORRECTION',
      desc="China's surprise yuan devaluation: Aug 24, 2015 the S&P plunged up to 5.3% intraday; extended by the early-2016 oil/EM rout."),
 dict(key='volmageddon', short='Volmageddon', name='Volmageddon',
      peak='2018-02', window='2018-01 – 2018-02', dd=-10.2, vix=37.3, nber=False, badge='CORRECTION',
      desc="Feb 5, 2018: the VIX's largest-ever one-day jump (+115%); short-volatility products destroyed (XIV −97% in a day)."),
 dict(key='q42018', short='Q4 2018', name='Q4 2018 Selloff',
      peak='2018-12', window='2018-09 – 2018-12', dd=-19.8, vix=36.1, nber=False, badge='BEAR',
      desc='Fed tightening, QT fears and the trade war: −19.8% to Christmas Eve — the worst December since 1931.'),
 dict(key='repo19', short='Repo \'19', name='September 2019 Repo Spasm',
      peak='2019-09', window='2019-09 – 2019-10', dd=-4.1, vix=None, nber=False, badge='STRESS EVENT',
      desc='Overnight repo rates spiked to 10% as reserves ran scarce, forcing $75B NY Fed injections; acute money-market fragility exposed.'),
 dict(key='covid20', short='COVID', name='COVID-19 Crash',
      peak='2020-03', window='2020-02 – 2020-04', dd=-33.9, vix=82.7, nber=True, badge='SEVERE BEAR + NBER RECESSION',
      desc='The fastest bear market on record: −33.9% in 33 days; VIX all-time record close 82.69; the shortest recession NBER ever dated.'),
 dict(key='terra22', short='Terra', name='Terra/LUNA Collapse',
      peak='2022-05', window='2022-05 – 2022-06', dd=-18.7, vix=None, nber=False, badge='CORRECTION',
      desc='The May 2022 TerraUSD death spiral cost crypto ~$18B that month and seeded the contagion (3AC, Celsius) that later felled FTX.'),
 dict(key='bear22', short='2022 bear', name='2022 Rate-Shock Bear Market',
      peak='2022-10', window='2022-01 – 2022-10', dd=-25.4, vix=36.5, nber=False, badge='BEAR',
      desc='The fastest Fed hiking cycle in four decades against generational inflation: −25.4% over nine months. No recession followed.'),
 dict(key='ftx22', short='FTX', name='FTX Collapse',
      peak='2022-11', window='2022-11 – 2022-12', dd=-22.4, vix=None, nber=False, badge='BEAR',
      desc="FTX's bankruptcy wiped ~$200B more of crypto value, capping a 12-month $2.2T (−73%) collapse in total crypto capitalization."),
 dict(key='svb23', short='SVB', name='SVB / Regional Banking Crisis',
      peak='2023-03', window='2023-03 – 2023-05', dd=-7.8, vix=26.5, nber=False, badge='STRESS EVENT',
      desc='A $42B one-day run collapsed SVB — the second-largest US bank failure ever — followed by Signature and the UBS takeover of Credit Suisse. IMF: not systemic.'),
 dict(key='yen24', short='Yen carry', name='Yen Carry-Trade Unwind',
      peak='2024-08', window='2024-07 – 2024-08', dd=-8.5, vix=38.6, nber=False, badge='STRESS EVENT',
      desc='A hawkish BOJ surprise forced a violent carry unwind on Aug 5, 2024: Nikkei −12.4% (worst day ever); VIX spiked to 65.7 intraday.'),
 dict(key='tariff25', short='Tariffs \'25', name='2025 Tariff Shock',
      peak='2025-04', window='2025-02 – 2025-04', dd=-18.9, vix=52.3, nber=False, badge='CORRECTION',
      desc='Sweeping tariffs announced Apr 2, 2025 triggered back-to-back −4.8% and −6.0% days; a 90-day pause ignited a 9.5% one-day rally.'),
 dict(key='ai25', short='AI scare', name='November 2025 AI-Valuation Scare',
      peak='2025-11', window='2025-11', dd=-5.1, vix=27.8, nber=False, badge='STRESS EVENT',
      desc='Doubts about AI-capex returns hit megacap tech: −5.1% in three weeks; VIX up ~50% on the month. November still closed positive.'),
 dict(key='iran26', short='Iran \'26', name='March 2026 Iran War Selloff',
      peak='2026-03', window='2026-02 – 2026-03', dd=-9.1, vix=None, nber=False, badge='STRESS EVENT',
      desc='US-Israeli strikes on Iran and Strait-of-Hormuz fears drove Brent above $80; −9.1%, stopping just short of correction. New highs by summer.'),
]

# FX-11 (expert debate): each episode is classified by whether the index CAN lead it.
# 'fragility' = crisis grown from inside the financial system (leverage, credit,
# valuation, funding) — the gauge should read elevated beforehand.
# 'shock' = exogenous hit (war, pandemic, policy surprise, sovereign/geopolitical) —
# a fragility gauge legitimately reads calm before these; on-date value is context only.
EVENT_KIND = {
    'early90s': 'shock',    'asia97': 'shock',    'ltcm98': 'fragility',
    'dotcom': 'fragility',  'sept11': 'shock',    'gfc08': 'fragility',
    'flash10': 'shock',     'euro11': 'shock',    'china15': 'shock',
    'volmageddon': 'fragility', 'q42018': 'shock', 'repo19': 'fragility',
    'covid20': 'shock',     'terra22': 'fragility', 'bear22': 'fragility',
    'ftx22': 'fragility',   'svb23': 'fragility', 'yen24': 'shock',
    'tariff25': 'shock',    'ai25': 'fragility',  'iran26': 'shock',
}


# ---------------------------------------------------------------- build
def main():
    status, series = {}, {}

    def grab(name, fn):
        try:
            series[name] = fn()
            status[name] = f'ok ({len(series[name])} obs, last {series[name].index[-1].date()})'
        except Exception as e:                                    # noqa: BLE001
            status[name] = f'FAILED: {type(e).__name__}: {e}'
        print(f'  {name}: {status[name]}', flush=True)

    for sid in ['T10Y3M', 'SAHMREALTIME', 'ICSA', 'BAA10Y', 'NFCI',
                'WALCL', 'WTREGEN', 'RRPONTSYD', 'M2SL', 'CSUSHPINSA',
                'PERMIT', 'MORTGAGE30US', 'DGS10', 'VIXCLS', 'DCOILBRENTEU', 'DFII10',
                'NFCINONFINLEVERAGE', 'NFCIRISK', 'STLFSI4', 'T10YIE', 'DRTSCILM']:
        grab(sid, lambda s=sid: fred(s))
    grab('OFR_FSI', lambda: ofr_col('OFR FSI'))
    grab('OFR_FUNDING', lambda: ofr_col('Funding'))
    grab('OFR_SAFE', lambda: ofr_col('Safe assets'))
    grab('OFR_EM', lambda: ofr_col('Emerging markets'))
    grab('OFR_ADV', lambda: ofr_col('Other advanced economies'))
    grab('CISS_US', lambda: ecb_ciss('US'))
    grab('CISS_EA', lambda: ecb_ciss('U2'))
    grab('VIX3M', lambda: cboe('VIX3M'))
    grab('SKEW', lambda: cboe('SKEW'))
    grab('GPR', gpr_monthly)
    grab('RECPROB', nyfed_recprob)
    grab('FUNDINGRATE', binance_funding)
    grab('STABLES', defillama_stables)
    grab('TVL', defillama_tvl)
    grab('MVRV', coinmetrics_mvrv)
    grab('EPU', epu)
    grab('CAPE', cape_multpl)

    W = {k: weekly(v) for k, v in series.items()}
    ind = {}   # name -> (score series 0-100, pillar, human label, raw formatter)

    def add(name, pillar, label, score, raw=None):
        score = score.dropna()
        if not len(score):
            print(f'  (dropping {name}: empty after transform)', flush=True)
            return
        # FX-8 percentile hygiene: a member with under 10y of effective history has
        # its percentile shrunk toward 50 by (years/10) and capped at 99 — a thin
        # window may never print a fake extreme (the ERP=100.0 class of error)
        yrs = len(score) / 52.0
        if yrs < 10:
            score = (50 + (score - 50) * (yrs / 10.0)).clip(1, 99)
        ind[name] = dict(pillar=pillar, label=label, score=score, raw=raw)

    # --- Macro (20%) — incl. v1.1: NY Fed probit + inflation de-anchoring
    if 'T10Y3M' in W:
        s = W['T10Y3M']
        add('curve', 'macro', '10y–3m curve (inverted)', roll_pct(-s), s)
        d13 = s - s.shift(13)
        was_inv = (s.shift(4).rolling(26, min_periods=8).min() < 0)
        steep = roll_pct(d13).where(was_inv, 50.0)
        add('resteep', 'macro', 'Curve re-steepening velocity', steep, d13)
    if 'SAHMREALTIME' in W:
        add('sahm', 'macro', 'Sahm rule', roll_pct(W['SAHMREALTIME']), W['SAHMREALTIME'])
    if 'ICSA' in W:
        c = W['ICSA'].rolling(4, min_periods=2).mean()
        yoy = c.pct_change(52) * 100
        add('claims', 'macro', 'Jobless claims 4-wk YoY', roll_pct(yoy), yoy)
    if 'RECPROB' in W:
        # already a calibrated probability — used directly, never percentile-ranked
        add('recprob', 'macro', 'NY Fed recession probability (12m)',
            (W['RECPROB'] * 100).clip(0, 100), W['RECPROB'] * 100)
    if 'T10YIE' in W:
        deanchor = (W['T10YIE'] - 2.0).abs()
        add('breakeven', 'macro', 'Inflation de-anchoring |10y BE − 2|',
            roll_pct(deanchor), W['T10YIE'])

    # --- Valuation (15%)
    if 'CAPE' in W:
        add('cape', 'valuation', 'Shiller CAPE', roll_pct(W['CAPE'], 1560, 520), W['CAPE'])
        if 'DFII10' in W:
            erp = (100.0 / W['CAPE']).reindex(W['DFII10'].index).ffill() - W['DFII10']
            add('erp', 'valuation', 'Equity risk premium (thin)', roll_pct(-erp), erp)

    # --- Credit & liquidity (25%)
    if 'BAA10Y' in W:
        # FX-7: Baa−10y spread is the scored long-history credit-spread carrier
        # (daily since 1986, keyless); the FRED-truncated 3y HY OAS was demoted
        add('baa', 'credit', 'Baa credit spread (two-sided)',
            two_sided(roll_pct(W['BAA10Y'])), W['BAA10Y'])
    if 'NFCI' in W:
        add('nfci', 'credit', 'Chicago Fed NFCI', roll_pct(W['NFCI']), W['NFCI'])
    if 'OFR_FSI' in W:
        add('ofr', 'credit', 'OFR Financial Stress Index', roll_pct(W['OFR_FSI']), W['OFR_FSI'])
    if all(k in W for k in ('WALCL', 'WTREGEN', 'RRPONTSYD')):
        idx = W['WALCL'].index
        nl = (W['WALCL'] - W['WTREGEN'].reindex(idx).ffill().fillna(0)
              - W['RRPONTSYD'].reindex(idx).ffill().fillna(0))
        d26 = nl.pct_change(26) * 100
        add('netliq', 'credit', 'Net liquidity 26-wk drain', roll_pct(-d26), d26)
    if 'M2SL' in W:
        yoy = W['M2SL'].pct_change(52) * 100
        add('m2', 'credit', 'M2 YoY (two-sided)', two_sided(roll_pct(yoy), 0.6), yoy)
    if 'OFR_FUNDING' in W:
        add('funding', 'credit', 'OFR funding stress', roll_pct(W['OFR_FUNDING']), W['OFR_FUNDING'])
    if 'OFR_SAFE' in W:
        add('safeassets', 'credit', 'OFR safe-asset demand', roll_pct(W['OFR_SAFE']), W['OFR_SAFE'])
    if 'CISS_US' in W:
        add('ciss_us', 'credit', 'US CISS (ECB, two-sided)',
            two_sided(roll_pct(W['CISS_US'])), W['CISS_US'])
    if 'NFCINONFINLEVERAGE' in W:
        add('nfcilev', 'credit', 'NFCI nonfinancial leverage (two-sided)',
            two_sided(roll_pct(W['NFCINONFINLEVERAGE'])), W['NFCINONFINLEVERAGE'])
    if 'STLFSI4' in W:
        add('stlfsi', 'credit', 'St. Louis Fed FSI', roll_pct(W['STLFSI4']), W['STLFSI4'])
    if 'DRTSCILM' in W:
        add('sloos', 'credit', 'SLOOS C&I tightening', roll_pct(W['DRTSCILM']), W['DRTSCILM'])

    # --- Housing (10%)
    if 'CSUSHPINSA' in W:
        yoy = W['CSUSHPINSA'].pct_change(52) * 100
        add('cs', 'housing', 'Case-Shiller YoY', roll_pct(yoy), yoy)
    if 'MORTGAGE30US' in W and 'DGS10' in W:
        sp = W['MORTGAGE30US'] - W['DGS10'].reindex(W['MORTGAGE30US'].index).ffill()
        add('mtg', 'housing', 'Mortgage spread', roll_pct(sp), sp)
    if 'PERMIT' in W:
        yoy = W['PERMIT'].pct_change(52) * 100
        add('permits', 'housing', 'Building permits YoY (inverted)', roll_pct(-yoy), yoy)

    # --- Crypto (10%)  (full-history percentiles, min 4y)
    if 'MVRV' in W:
        add('mvrv', 'crypto', 'BTC MVRV', roll_pct(W['MVRV'], 10000, 208), W['MVRV'])
    if 'STABLES' in W:
        d13 = W['STABLES'].pct_change(13) * 100
        add('stables', 'crypto', 'Stablecoin supply 13-wk (stall = risk)',
            roll_pct(-d13, 10000, 156), d13)
    if 'TVL' in W:
        d13 = W['TVL'].pct_change(13) * 100
        add('tvl', 'crypto', 'DeFi TVL 13-wk trend (inverted)',
            roll_pct(-d13, 10000, 156), d13)
    if 'FUNDINGRATE' in W:
        f30 = W['FUNDINGRATE'].rolling(4, min_periods=2).mean() * 100
        add('funding_perp', 'crypto', 'BTC perp funding 30d (two-sided)',
            two_sided(roll_pct(f30, 10000, 104), 0.6), f30)

    # --- Sentiment (10%)
    if 'VIXCLS' in W:
        add('vix', 'sentiment', 'VIX (two-sided)',
            two_sided(roll_pct(W['VIXCLS'])), W['VIXCLS'])
    if 'VIXCLS' in W and 'VIX3M' in W:
        ts = W['VIXCLS'] / W['VIX3M'].reindex(W['VIXCLS'].index).ffill()
        add('vixterm', 'sentiment', 'VIX term structure (backwardation)',
            roll_pct(ts, 10000, 156), ts)
    if 'SKEW' in W:
        # FX-19: complacency-side only — low SKEW (no tail hedging) scores risk
        add('skew', 'sentiment', 'CBOE SKEW (complacency-side)',
            roll_pct(-W['SKEW']), W['SKEW'])

    # --- Geopolitics (10%) — v1.1: GPR primary, CISS-EA + OFR regional legs
    if 'GPR' in W:
        add('gpr', 'geo', 'Geopolitical Risk index (Caldara-Iacoviello)',
            roll_pct(W['GPR']), W['GPR'])
    if 'CISS_EA' in W:
        add('ciss_ea', 'geo', 'Euro-area CISS (ECB)', roll_pct(W['CISS_EA']), W['CISS_EA'])
    if 'OFR_EM' in W:
        add('em_stress', 'geo', 'OFR emerging-market stress', roll_pct(W['OFR_EM']), W['OFR_EM'])
    if 'OFR_ADV' in W:
        add('adv_stress', 'geo', 'OFR other-advanced-economies stress',
            roll_pct(W['OFR_ADV']), W['OFR_ADV'])
    if 'EPU' in W:
        avg = W['EPU'].rolling(13, min_periods=4).mean()
        add('epu', 'geo', 'Economic Policy Uncertainty 13-wk', roll_pct(avg), avg)
    if 'DCOILBRENTEU' in W:
        d13 = W['DCOILBRENTEU'].pct_change(13) * 100
        add('oil', 'geo', 'Brent 13-wk shock', roll_pct(d13), d13)

    # ---------------- composite
    PW = dict(credit=.25, macro=.20, valuation=.15, housing=.10,
              crypto=.10, sentiment=.10, geo=.10)
    frame = pd.DataFrame({k: v['score'] for k, v in ind.items()})
    cutoff = pd.Timestamp.now() + pd.Timedelta(days=7)   # no future-dated rows, ever
    frame = frame[(frame.index >= '1990-01-01') & (frame.index <= cutoff)]
    # FX-2 (expert debate 2026-09-02): per-MEMBER LOCF before any pillar math.
    # Without this, the frame's union-dated tail rows contain only the members that
    # updated most recently, and every pillar is silently impersonated by its
    # latest-updating member (geo was EPU alone; credit was one spread series).
    LOCF_LIMIT_WEEKS = {'sloos': 17, 'recprob': 17}      # structurally quarterly
    for col in frame.columns:
        frame[col] = frame[col].ffill(limit=LOCF_LIMIT_WEEKS.get(col, 13))

    # FX-9: credit aggregates as equal-weight SUB-BUCKETS so four correlated Fed
    # composites can't outvote a screaming leverage signal
    CREDIT_BUCKETS = {'baa': 'spreads',
                      'nfci': 'fci', 'ofr': 'fci', 'stlfsi': 'fci', 'ciss_us': 'fci',
                      'safeassets': 'fci',
                      'nfcilev': 'leverage',
                      'netliq': 'liquidity', 'funding': 'liquidity', 'm2': 'liquidity',
                      'sloos': 'supply'}
    pillar_hist = {}
    for p in PW:
        cols = [k for k, v in ind.items() if v['pillar'] == p and k in frame]
        if not cols:
            pillar_hist[p] = pd.Series(dtype=float)
        elif p == 'credit':
            buckets = {}
            for k in cols:
                buckets.setdefault(CREDIT_BUCKETS.get(k, 'other'), []).append(k)
            bmeans = [frame[ks].mean(axis=1) for ks in buckets.values()]
            pillar_hist[p] = pd.concat(bmeans, axis=1).mean(axis=1)
        else:
            pillar_hist[p] = frame[cols].mean(axis=1)
    ph = pd.DataFrame(pillar_hist)   # members are already LOCF'd; no pillar-level fill

    wts = pd.Series(PW)
    avail = ph.notna()
    wsum = (avail * wts).sum(axis=1)
    comp_raw = ((ph.fillna(0) * wts).sum(axis=1) / wsum).where(wsum >= 0.60)
    core = avail[['credit', 'macro']].all(axis=1)
    comp_raw = comp_raw.where(core).dropna()
    if not len(comp_raw):
        print('DIAGNOSTIC: composite empty.', flush=True)
        print('indicator frame shape:', frame.shape, flush=True)
        print('last row of pillar scores:\n', ph.tail(3), flush=True)
        print('weight sum tail:\n', wsum.tail(3), flush=True)
        sys.exit('composite empty — see diagnostics above')
    comp_raw.index = pd.to_datetime(comp_raw.index)   # guard: resample needs a DatetimeIndex
    comp = comp_raw.ewm(span=4).mean()
    velocity = comp - comp.shift(13)

    # --- CISS-style correlation layer (Hollo/Kremer/Lo Duca, ECB WP 1426 eq. 2-4).
    # Published ALONGSIDE the linear composite, never replacing it. Breadth ~1 means
    # stress is broad-based/correlated across pillars; ~0 means idiosyncratic.
    breadth_now, systemic_now = None, None
    try:
        Pn = (ph[list(PW.keys())].reindex(comp_raw.index) / 100.0)
        Pn = Pn.fillna(0.5)                       # missing pillar = neutral, not stress
        wv = np.array([PW[p] for p in PW])
        Sd = Pn.values - 0.5                      # demean by theoretical median
        burn = Pn.index < pd.Timestamp('1995-01-01')
        seed_rows = Sd[burn] if burn.any() else Sd[:52]
        Cm = np.mean([np.outer(s, s) for s in seed_rows], axis=0) + np.eye(len(wv)) * 1e-6
        lam = 0.93
        sys_vals, br_vals = [], []
        for trow in Sd:
            Cm = lam * Cm + (1 - lam) * np.outer(trow, trow)
            sd = np.sqrt(np.clip(np.diag(Cm), 1e-9, None))
            Rm = Cm / np.outer(sd, sd)
            y = wv * (trow + 0.5)
            v = float(y @ Rm @ y)
            sys_vals.append(v)
            denom = float(y.sum()) ** 2
            br_vals.append(min(1.0, max(0.0, v / denom)) if denom > 1e-9 else 0.0)
        systemic = pd.Series(np.sqrt(np.clip(sys_vals, 0, None)) * 100,
                             index=Pn.index).ewm(span=4).mean()
        breadth = pd.Series(br_vals, index=Pn.index).ewm(span=4).mean()
        systemic_now = round(float(systemic.iloc[-1]), 1)
        breadth_now = round(float(breadth.iloc[-1]), 2)
    except Exception as e:                                        # noqa: BLE001
        print(f'  (systemic layer failed: {e})', flush=True)

    now = comp.index[-1]
    gci = round(float(comp.iloc[-1]), 1)
    vel = round(float(velocity.iloc[-1]), 1)
    disp = float((comp_raw - comp).tail(13).std())
    half = round(min(4.0, max(1.2, 1.2 + 1.5 * disp)), 1)
    bands = ['CALM', 'NORMAL', 'ELEVATED', 'HIGH-STRESS', 'CRITICAL']
    bi = int(min(gci, 99.9) // 20)

    # pillar detail + freshness
    pillars_out = {}
    for p in PW:
        cols = [k for k, v in ind.items() if v['pillar'] == p and k in frame]
        members = []
        for k in cols:
            sc = ind[k]['score']
            raw = ind[k]['raw']
            carried = frame[k].dropna()
            members.append(dict(
                key=k, label=ind[k]['label'],
                score=(round(float(carried.iloc[-1]), 1) if len(carried)
                       else round(float(sc.iloc[-1]), 1)),
                asof=str(sc.index[-1].date()),
                raw=(round(float(raw.dropna().iloc[-1]), 2)
                     if raw is not None and len(raw.dropna()) else None),
                live=bool(pd.notna(frame[k].iloc[-1])),
                stale=bool((now - sc.index[-1]).days > 40)))
        pscore = round(float(ph[p].iloc[-1]), 1) if p in ph and not np.isnan(ph[p].iloc[-1]) else None
        pillars_out[p] = dict(weight=PW[p], score=pscore, members=members)

    # weekly movers: Δ pillar-member score × effective weight
    movers = []
    for k, v in ind.items():
        sc = v['score']
        if len(sc) < 2 or (now - sc.index[-1]).days > 14:
            continue
        n_in_pillar = max(1, sum(1 for kk, vv in ind.items()
                                 if vv['pillar'] == v['pillar'] and kk in frame))
        eff = PW[v['pillar']] / n_in_pillar
        d = float(sc.iloc[-1] - sc.iloc[-2]) * eff
        if abs(d) >= 0.05:
            movers.append(dict(key=k, label=v['label'], impact=round(d, 2),
                               from_=round(float(sc.iloc[-2]), 1),
                               to=round(float(sc.iloc[-1]), 1)))
    movers.sort(key=lambda m: -abs(m['impact']))

    # monthly-thinned history for the chart/scrubber
    monthly = comp.resample('ME').last().dropna()
    hist = [[round(d.year + (d.month - 0.5) / 12, 3), round(float(v), 1)]
            for d, v in monthly.items()]
    # weekly tail (~5y) for the trend tile's range selector
    hist_w = [[round(d.year + (d.dayofyear - 1) / 365.25, 4), round(float(v), 1)]
              for d, v in comp.tail(270).items()]

    # events: verified chronology anchored to the composite's own local peak near each
    # episode's documented peak-stress month
    events, events_all = [], []
    for ev in EVENTS_VERIFIED:
        # anchor at the reading in the DOCUMENTED month, not the local max of a wide
        # window — a windowed max collapses same-quarter episodes (2022 bear vs FTX)
        # onto one point. Nearest weekly observation to the event's own month.
        d = pd.to_datetime(ev['peak'] + '-15')
        near = comp[(comp.index >= d - pd.Timedelta(days=30)) &
                    (comp.index <= d + pd.Timedelta(days=30))]
        if not len(near):
            continue
        peak_d = near.index[(near.index - d).map(abs).argmin()]
        # the event's reading is the SAME monthly value the dial/chart/scorecard show for
        # that month — one number per month everywhere, never a weekly-vs-monthly mismatch
        same_month = monthly[monthly.index.to_period('M') == peak_d.to_period('M')]
        peak_v = float(same_month.iloc[0]) if len(same_month) else float(comp.loc[peak_d])
        t = round(peak_d.year + (peak_d.dayofyear - 1) / 365.25, 4)
        major = 'BEAR' in ev['badge']
        parts = [ev['name'].upper(), f"S&P {ev['dd']:+.1f}%"]
        if ev['vix']:
            parts.append(f"VIX {ev['vix']:.1f}")
        fact = ' · '.join(parts)
        if major:
            events.append([t, ev['short'], round(peak_v, 1)])
        events_all.append(dict(t=t, key=ev['key'], short=ev['short'], name=ev['name'],
                               v=round(peak_v, 1), badge=ev['badge'], dd=ev['dd'],
                               vix=ev['vix'], nber=ev['nber'], window=ev['window'],
                               desc=ev['desc'], fact=fact, kind=EVENT_KIND.get(ev['key'], 'shock')))

    out = dict(
        generated=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        asof=str(now.date()), gci=gci, band=bands[bi], band_index=bi,
        velocity13w=vel, range_half=half,
        breadth=breadth_now, gci_systemic=systemic_now,
        pillars=pillars_out, movers=movers[:8], history=hist, history_w=hist_w,
        events=events, events_all=events_all,
        source_status=status,
        note='Weekly composite; percentile method per design doc; pillars '
             'renormalize over available indicators; crypto active from ~2015.')
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=1)

    print(f'GCI {gci} ({bands[bi]})  velocity13w {vel:+}  asof {now.date()}')
    print(f'history {len(hist)} months from {hist[0][0]}  events {len(events)}')
    for k, v in status.items():
        flag = 'OK ' if v.startswith('ok') else '!! '
        print(f'  {flag}{k}: {v}')

if __name__ == '__main__':
    sys.exit(main())
