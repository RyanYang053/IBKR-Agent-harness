"""Deterministic investment analysis. No ledger writes, orders, or invented prices."""
from datetime import date, timedelta
from decimal import Decimal
import json, math
from .core import config, db
from .finance import day, dec, performance, portfolio, schema as finance_schema
from . import knowledge, market

def _ready(require_fresh=False):
    snap=portfolio()
    if 'nav' not in snap or (require_fresh and snap.get('status')!='ok'):
        return snap, {'status':'blocked','reason':snap.get('status','unavailable'),'stale':snap.get('stale'),'limitations':['Current estimates are blocked until every account shares one fresh statement date.']}
    return snap, None

def exposure(account=None):
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    rows=[p for p in snap.get('positions',[]) if not account or p['account']==account]
    if not rows: return {'status':'no_positions','as_of':snap.get('as_of'),'limitations':['No summary positions are stored for this filter.']}
    gross=sum((abs(dec(p['value_base'])) for p in rows),Decimal(0))
    net=sum((dec(p['value_base']) for p in rows),Decimal(0))
    by_conid={}
    shorts=0; fx=Decimal(0)
    for p in rows:
        key=p.get('underlying_conid') or p['conid']
        by_conid[key]=by_conid.get(key,Decimal(0))+dec(p['value_base'])
        if dec(p['quantity'])<0: shorts+=1
        if p.get('currency') and p['currency']!=snap['currency']: fx+=dec(p['value_base'])
    nav=dec(snap['nav']) if snap.get('nav') else None
    weights=[]
    for conid,value in sorted(by_conid.items(), key=lambda item: abs(item[1]), reverse=True):
        weights.append({'underlying_conid':conid,'value_base':str(value),'weight_of_nav':None if not nav else str(value/nav)})
    return {'status':'ok' if snap.get('status')=='ok' else snap.get('status'),'as_of':snap.get('as_of'),'currency':snap.get('currency'),'gross_value_base':str(gross),'net_value_base':str(net),'gross_to_net':None if net==0 else str(gross/abs(net)),'by_underlying':weights[:50],'short_positions':shorts,'fx_value_base':str(fx),'greeks':'unavailable','effective_leverage':'not_computed','etf_look_through':'unknown','benchmark':'unavailable','limitations':['Gross and net values are signed position market values, not option delta or financing leverage. ETF holdings look-through is unknown. No benchmark prices are configured.']}

def price_shock(shock, account=None):
    """Apply one stated price shock to every stored position value. This is not an index or option reprice."""
    base=exposure(account)
    if base.get('status') not in {'ok','partial_or_stale'}: return base
    rate=dec(shock)
    if rate<=Decimal('-1') or rate>Decimal('1'): raise ValueError('shock must be greater than -100 percent and at most 100 percent')
    shocked=[]
    for row in base['by_underlying']:
        value=dec(row['value_base'])
        shocked.append({**row,'shocked_value_base':str(value*(1+rate))})
    return {'status':base['status'],'as_of':base['as_of'],'currency':base['currency'],'shock':str(rate),'method':'uniform_position_value_shock','rows':shocked,'limitations':base['limitations']+['A uniform position shock is not a QQQ, TQQQ, or option scenario. Path-dependent leverage is not modeled without a supplied price path.']}

def hypothetical_trade(conid, quantity, price, currency, fx_rate='1'):
    """Compare the current snapshot with one proposed trade. Nothing is submitted."""
    snap, blocked=_ready(require_fresh=True)
    if blocked: return blocked
    qty, px, fx=dec(quantity), dec(price), dec(fx_rate)
    if px<=0 or fx<=0: raise ValueError('price and fx rate must be positive')
    value=qty*px*fx
    current=next((p for p in snap['positions'] if p['conid']==str(conid)), None)
    current_value=dec(current['value_base']) if current else Decimal(0)
    nav=dec(snap['nav'])
    return {'status':'proposal','as_of':snap['as_of'],'currency':snap['currency'],'conid':str(conid),'trade_currency':currency,'current_value_base':str(current_value),'proposed_value_base':str(value),'combined_value_base':str(current_value+value),'weight_after':str((current_value+value)/nav) if nav else None,'orders':'disabled','limitations':['Proposal only. Price and quantity are caller inputs, not a live quote. No margin or option model is applied.']}

def risk():
    perf=performance()
    if perf.get('twr') is None:
        return {'status':perf.get('status','unavailable'),'max_drawdown':None,'var_95':None,'sharpe':None,'limitations':perf.get('limitations',[])}
    level=Decimal(1); peak=Decimal(1); worst=Decimal(0); returns=[]
    for item in perf.get('intervals') or []:
        r=dec(item['interval_return']); returns.append(r); level*=1+r
        if level>peak: peak=level
        worst=min(worst, level/peak-1)
    ordered=sorted(returns)
    observations=len(ordered)
    var=None
    if observations>=20:
        index=max(0, int(observations*Decimal('0.05'))-1)
        var=ordered[index]
    rate=None
    cfg_rate=config().get('finance',{}).get('risk_free_rate')
    sharpe_source='config' if cfg_rate is not None else None
    if cfg_rate is None:
        point=market.macro('DGS3MO')
        if point.get('value'):
            cfg_rate=dec(point['value'])/Decimal(100)
            sharpe_source='FRED DGS3MO'
    if observations>=20:
        mean=sum(returns, Decimal(0))/Decimal(observations)
        var_pop=sum((r-mean)**2 for r in returns)/Decimal(observations)
        if var_pop>0 and cfg_rate is not None:
            daily=dec(cfg_rate)/Decimal(252)
            rate=(mean-daily)/var_pop.sqrt()*Decimal(252).sqrt()
    qqq=market.bars('QQQ', 5)
    return {'status':'ok','as_of':perf.get('as_of'),'currency':perf.get('currency'),'max_drawdown':str(worst),'drawdown_method':'cumulative_daily_approximation_not_raw_nav','var_95':None if var is None else str(var),'var_method':'historical_empirical_daily_approximation','observations':observations,'sharpe':None if rate is None else str(rate),'sharpe_status':'unavailable' if rate is None else 'computed','sharpe_source':sharpe_source,'beta':'unavailable','benchmark':'unavailable' if not qqq else 'price_history_only','qqq_bars':len(qqq),'fx':market.fx(),'twr_exact':False,'limitations':['Drawdown and VaR use the daily-approximation portfolio returns, not security prices. Sharpe uses a configured rate or cached FRED DGS3MO. QQQ bars, when present, are historical price returns and are not a CAD total-return benchmark.']}

def dcf(fcff, wacc, growth, net_debt, shares):
    """One-stage Gordon growth. FCFF is next year's explicit free cash flow, not a forecast from filings."""
    flow, discount, terminal, debt, count=dec(fcff), dec(wacc), dec(growth), dec(net_debt), dec(shares)
    if count<=0 or discount<=0 or discount<=terminal: raise ValueError('shares and the WACC spread must be positive')
    enterprise=flow/(discount-terminal)
    equity=enterprise-debt
    return {'method':'one_stage_gordon','fcff_timing':'next_year','enterprise_value':str(enterprise),'equity_value':str(equity),'per_share':str(equity/count),'terminal_share_of_ev':'1','price':'unavailable','price_freshness':'unavailable','upside':'unavailable','limitations':['Terminal value is the entire enterprise value. Upside is withheld unless a live quote is supplied.']}

def attach_market_price(row, symbol, fetch_live=False):
    quote=market.latest_quote(symbol) if (fetch_live and market.alpaca_configured()) else market.quote(symbol)
    priced=dict(row)
    priced['price_quote']={'symbol':quote.get('symbol'),'price':quote.get('price'),'freshness':quote.get('freshness'),'provider':quote.get('provider'),'as_of':quote.get('as_of')}
    if quote.get('freshness')=='live' and quote.get('price'):
        px=dec(quote['price'])
        priced['price']=quote['price']
        priced['price_freshness']='live'
        priced['upside']=str(dec(row['per_share'])/px-1) if px>0 else 'unavailable'
    else:
        priced['price']=quote.get('price') or 'unavailable'
        priced['price_freshness']=quote.get('freshness') or 'unavailable'
        priced['upside']='withheld'
        priced['limitations']=list(row.get('limitations') or [])+['A delayed, historical, or missing price is not used as a live upside.']
    return priced

def _tax_bucket(alias):
    explicit=(config().get('finance',{}).get('account_tax_types') or {}).get(alias)
    label=str(explicit or alias or '').strip().casefold()
    if 'tfsa' in label: return 'tfsa'
    if 'rrsp' in label or 'rrif' in label: return 'rrsp'
    if 'fhsa' in label: return 'fhsa'
    if any(k in label for k in ('taxable','margin','individual','non_registered')): return 'taxable'
    return 'unspecified'

def _tax_tag(bucket, pl):
    if pl is None: return bucket+'_cost_basis_unavailable'
    if bucket=='taxable': return 'taxable_unrealized_gain' if pl>0 else 'taxable_loss_harvest_candidate' if pl<0 else 'taxable_flat'
    if bucket in {'tfsa','rrsp','fhsa'}: return bucket+('_tax_sheltered_gain' if pl>=0 else '_non_deductible_loss')
    return 'unspecified_account_tax_type'

def tax_positions(account=None, symbol=None):
    """Per-stock NAV weights, account tax location, statement unrealized P/L, superficial-loss flags, and dividend withholding."""
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    target_sym=str(symbol or '').strip().upper() or None
    all_positions=snap.get('positions') or []
    nav=dec(snap['nav']) if snap.get('nav') else None
    as_of_date=date.fromisoformat(snap['as_of'])
    cutoff=(as_of_date-timedelta(days=30)).isoformat()
    div_by_key={}; wht_by_key={}; recent_buys=set()
    with db('finance') as c:
        finance_schema(c)
        for row in c.execute("SELECT account, category, payload FROM records WHERE category IN ('cash','trade')"):
            fields=json.loads(row['payload']); cid=str(fields.get('conid') or '')
            if not cid: continue
            if row['category']=='cash':
                kind=str(fields.get('type') or '').strip().casefold()
                amt=dec(fields.get('amount') or fields.get('amountInBase') or '0')
                if 'dividend' in kind: div_by_key[(row['account'],cid)]=div_by_key.get((row['account'],cid),Decimal(0))+amt
                elif 'withholding' in kind: wht_by_key[(row['account'],cid)]=wht_by_key.get((row['account'],cid),Decimal(0))+amt
            elif row['category']=='trade':
                side=str(fields.get('buySell') or '').strip().upper()
                raw_dt=fields.get('tradeDate') or fields.get('reportDate') or fields.get('dateTime') or ''
                try:
                    if side=='BUY' and raw_dt and day(raw_dt)>=cutoff: recent_buys.add(cid)
                except Exception:
                    pass
    grouped={}
    for p in all_positions:
        grouped.setdefault(p['conid'],[]).append(p)
    items=[]
    for cid, lots in grouped.items():
        sym=str(lots[0].get('symbol') or '').upper()
        if target_sym and sym!=target_sym: continue
        filtered=[p for p in lots if not account or p['account']==account]
        if not filtered: continue
        buckets={_tax_bucket(p['account']) for p in lots}
        cross_shelter='taxable' in buckets and bool(buckets & {'tfsa','rrsp','fhsa'})
        lot_rows=[]; total_val=Decimal(0); total_qty=Decimal(0); tax_val=Decimal(0); reg_val=Decimal(0); total_pl=Decimal(0); has_pl=False
        for p in filtered:
            val=dec(p['value_base']); qty=dec(p['quantity']); fx=dec(p.get('fx_rate') or '1'); bucket=_tax_bucket(p['account'])
            total_val+=val; total_qty+=qty
            if bucket=='taxable': tax_val+=val
            elif bucket in {'tfsa','rrsp','fhsa'}: reg_val+=val
            cost_local=dec(p['cost_basis']) if p.get('cost_basis') not in (None,'') else None
            cost_base=cost_local*fx if cost_local is not None else None
            pl_base=val-cost_base if cost_base is not None else None
            if pl_base is not None: total_pl+=pl_base; has_pl=True
            lot_rows.append({'account':p['account'],'tax_bucket':bucket,'quantity':str(qty),'value_base':str(val),'weight_of_nav':str(val/nav) if nav else None,'cost_basis_local':None if cost_local is None else str(cost_local),'cost_basis_base_statement_fx':None if cost_base is None else str(cost_base),'unrealized_pl_base_statement_fx':None if pl_base is None else str(pl_base),'tax_implication':_tax_tag(bucket,pl_base),'dividends_local':str(div_by_key.get((p['account'],cid),Decimal(0))),'withholding_tax_local':str(wht_by_key.get((p['account'],cid),Decimal(0)))})
        q=market.quote(sym) if (lots[0].get('asset') or '').upper()=='STK' else {'freshness':'unavailable','price':None}
        superficial=cross_shelter or (cid in recent_buys and 'taxable' in buckets)
        reasons=[]
        if cross_shelter: reasons.append('Held in both taxable and registered accounts; selling a taxable loss risks the 30-day Canadian superficial-loss rule if registered shares are retained or added.')
        if cid in recent_buys and 'taxable' in buckets: reasons.append('BUY execution recorded within 30 days of statement date.')
        if 'tfsa' in buckets and str(lots[0].get('currency') or '').upper()=='USD': reasons.append('US dividends in TFSA remain subject to 15% IRS withholding with no Canadian foreign tax credit.')
        items.append({'conid':cid,'symbol':sym,'asset':lots[0].get('asset'),'currency':lots[0].get('currency'),'total_quantity':str(total_qty),'total_value_base':str(total_val),'weight_of_nav':str(total_val/nav) if nav else None,'taxable_weight_of_nav':str(tax_val/nav) if nav else None,'registered_weight_of_nav':str(reg_val/nav) if nav else None,'unrealized_pl_base_statement_fx':str(total_pl) if has_pl else None,'superficial_loss_watch':superficial,'tax_notes':reasons,'quote':{'price':q.get('price'),'currency':q.get('currency'),'freshness':q.get('freshness'),'as_of':q.get('as_of')},'lots':lot_rows})
    items.sort(key=lambda r:abs(dec(r['total_value_base'])), reverse=True)
    return {'status':'ok' if snap.get('status')=='ok' else snap.get('status'),'as_of':snap.get('as_of'),'advice_allowed':snap.get('advice_allowed'),'currency':snap.get('currency'),'nav':snap.get('nav'),'count':len(items),'positions':items[:50],'limitations':['Cost basis in CAD multiplies Flex costBasisMoney by statement-date fxRateToBase; exact Canadian tax ACB requires settlement-date FX per lot and cross-brokerage identical-property pooling.','Delayed or missing quotes are never substituted for live prices.']}

def _edgar_snapshot(symbol):
    rows=market.filings(symbol)
    if not rows: return None
    latest={}
    for r in rows:
        latest.setdefault(r['concept'], {'period':r['period'],'value':r['value'],'form':r['form']})
    return latest

def _bar_returns(symbol, limit=120):
    rows=market.bars(symbol, limit)
    out={}
    for prev, cur in zip(rows, rows[1:]):
        try:
            p0=float(prev['close']); p1=float(cur['close'])
            if p0>0: out[cur['date']]=(p1/p0)-1.0
        except Exception:
            continue
    return out

def _pairwise_corr(ret_a, ret_b):
    common=sorted(set(ret_a) & set(ret_b))
    if len(common)<20: return None, len(common)
    xs=[ret_a[d] for d in common]; ys=[ret_b[d] for d in common]
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    vx=sum((x-mx)**2 for x in xs); vy=sum((y-my)**2 for y in ys)
    if vx<=0 or vy<=0: return None, len(common)
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    return cov/math.sqrt(vx*vy), len(common)

def stock_brief(symbol, fcff='', wacc='', growth='', net_debt='', shares=''):
    """Unified single-stock context for customized queries: tax-weighted holdings, quote freshness, bars, news, earnings, EDGAR facts, optional DCF, correlated peers, and local notes."""
    sym=str(symbol or '').strip().upper()
    if not sym: raise ValueError('symbol is required')
    tax_all=tax_positions()
    held_matches=[p for p in (tax_all.get('positions') or []) if p['symbol']==sym]
    bars=market.bars(sym, 60)
    closes=[dec(b['close']) for b in bars if b.get('close') is not None]
    bar_summary={'rows':len(bars),'freshness':'historical','latest_date':bars[-1]['date'] if bars else None,'latest_close':bars[-1]['close'] if bars else None,'window_low':str(min(closes)) if closes else None,'window_high':str(max(closes)) if closes else None,'provider':bars[-1]['provider'] if bars else None}
    sym_rets=_bar_returns(sym, 90)
    corr_peers=[]
    if len(sym_rets)>=20:
        for p in (tax_all.get('positions') or []):
            psym=p['symbol']
            if psym==sym or (p.get('asset') or '').upper()!='STK': continue
            rho, n_obs=_pairwise_corr(sym_rets, _bar_returns(psym, 90))
            if rho is not None and rho>=0.60:
                corr_peers.append({'symbol':psym,'corr_90d':round(rho,4),'observations':n_obs,'held_weight_of_nav':str(round(dec(p['weight_of_nav']),4)) if p.get('weight_of_nav') else None})
        corr_peers.sort(key=lambda r:r['corr_90d'], reverse=True)
    valuation=None
    if all(str(v).strip()!='' for v in (fcff,wacc,growth,net_debt,shares)):
        valuation=attach_market_price(dcf(fcff,wacc,growth,net_debt,shares), sym, fetch_live=False)
    return {
        'symbol':sym,
        'portfolio_gate':{'status':tax_all.get('status'),'as_of':tax_all.get('as_of'),'advice_allowed':tax_all.get('advice_allowed'),'currency':tax_all.get('currency')},
        'held':bool(held_matches),
        'tax_weighted_positions':held_matches,
        'correlated_portfolio_peers':corr_peers[:6],
        'quote':market.quote(sym),
        'historical_bars_60d':bar_summary,
        'company_news':market.news(sym, 5),
        'earnings_calendar':market.earnings(sym),
        'sec_edgar_summary':_edgar_snapshot(sym),
        'dcf':valuation,
        'behavioral_guardrails':knowledge.memory_guardrails(sym, 6),
        'local_notes':knowledge.search(sym, 3),
        'approved_memory':knowledge.recall(sym, 3),
        'orders':'disabled',
        'limitations':tax_all.get('limitations',[])+['Suggestions must state quote freshness explicitly, check behavioral_guardrails, and withhold precise current trade advice when advice_allowed is false.']
    }

def trading_habits(account=None):
    """Deterministic audit of trading execution habits, sizing concentration, gain/loss holding asymmetry, tax structure, and thesis coverage."""
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    tax=tax_positions(account=account)
    if tax.get('status') not in {'ok','partial_or_stale'}: return tax
    acct_rows=[a for a in snap.get('accounts',[]) if not account or a['account']==account]
    acct_nav=sum((dec(a['nav']) for a in acct_rows), Decimal(0))
    cash_total=sum((dec(a['cash']) for a in acct_rows if a.get('cash') not in (None,'')), Decimal(0))
    positions=tax.get('positions') or []
    weights=sorted([abs(dec(p['total_value_base']))/acct_nav for p in positions if acct_nav>0], reverse=True)
    top1=weights[0] if weights else Decimal(0)
    top3=sum(weights[:3], Decimal(0))
    tail_count=sum(1 for w in weights if Decimal(0)<w<Decimal('0.01'))
    win_lots=0; loss_lots=0; gross_gain=Decimal(0); gross_loss=Decimal(0); deep_losers=[]; tfsa_wht_syms=set(); harvest_count=0
    for item in positions:
        for lot in item.get('lots') or []:
            pl_raw=lot.get('unrealized_pl_base_statement_fx'); cost_raw=lot.get('cost_basis_base_statement_fx')
            if pl_raw is not None:
                pl=dec(pl_raw)
                if pl>0: win_lots+=1; gross_gain+=pl
                elif pl<0:
                    loss_lots+=1; gross_loss+=pl
                    if lot.get('tax_bucket')=='taxable': harvest_count+=1
                    if cost_raw and dec(cost_raw)>0:
                        pct=pl/dec(cost_raw)
                        if pct<=Decimal('-0.20'):
                            deep_losers.append({'symbol':item['symbol'],'account':lot['account'],'return_vs_cost':str(round(pct,4)),'unrealized_pl_base':str(round(pl,2))})
            if lot.get('tax_bucket')=='tfsa' and dec(lot.get('withholding_tax_local') or '0')!=0:
                tfsa_wht_syms.add(item['symbol'])
    deep_losers.sort(key=lambda r:dec(r['return_vs_cost']))
    trade_count=0; fx_conv_count=0; buys=0; sells=0; comms=Decimal(0); by_sym={}; timeline={}
    with db('finance') as c:
        finance_schema(c)
        q="SELECT account, payload FROM records WHERE category='trade'"+(" AND account=?" if account else "")
        for row in c.execute(q, (account,) if account else ()):
            f=json.loads(row['payload'])
            if str(f.get('assetCategory') or 'STK').upper()=='CASH':
                fx_conv_count+=1; continue
            trade_count+=1
            side=str(f.get('buySell') or '').strip().upper()
            if side=='BUY': buys+=1
            elif side=='SELL': sells+=1
            sym=str(f.get('symbol') or f.get('conid') or 'UNKNOWN').upper()
            by_sym[sym]=by_sym.get(sym,0)+1
            if f.get('ibCommission') not in (None,''): comms+=abs(dec(f['ibCommission']))
            raw_dt=f.get('tradeDate') or f.get('reportDate') or f.get('dateTime') or ''
            cid=str(f.get('conid') or sym)
            try:
                if raw_dt and side in {'BUY','SELL'}: timeline.setdefault((row['account'],cid,sym),[]).append((date.fromisoformat(day(raw_dt)),side))
            except Exception:
                pass
    rt_7d=0; rt_30d=0; churn_syms=set()
    for (_acct,_cid,sym), events in timeline.items():
        events.sort(key=lambda x:x[0])
        last_buy=None
        for dt, side in events:
            if side=='BUY': last_buy=dt
            elif side=='SELL' and last_buy is not None:
                gap=(dt-last_buy).days
                if 0<=gap<=7: rt_7d+=1; churn_syms.add(sym)
                if 0<=gap<=30: rt_30d+=1; churn_syms.add(sym)
                last_buy=None
    decisions=knowledge.list_memory('decision', limit=100)
    covered_syms=set()
    for d in decisions:
        sym=str(d.get('symbol') or '').upper()
        try:
            parsed=json.loads(d['content']); sym=sym or str(parsed.get('instrument') or '').upper()
        except Exception:
            pass
        if sym: covered_syms.add(sym)
    open_stk=sorted({p['symbol'] for p in positions if (p.get('asset') or '').upper()=='STK'})
    untracked=[s for s in open_stk if s not in covered_syms]
    coverage=Decimal(len(open_stk)-len(untracked))/Decimal(len(open_stk)) if open_stk else Decimal(1)
    overlaps=[p['symbol'] for p in positions if p.get('superficial_loss_watch')]
    findings=[]
    if top3>=Decimal('0.45'):
        findings.append({'code':'top3_concentration','priority':'high','metric':str(round(top3,4)),'action':'Top 3 holdings exceed 45% of shown NAV; stress-test downside with get_scenario before adding.'})
    if deep_losers:
        findings.append({'code':'drawdown_anchoring_check','priority':'high','metric':len(deep_losers),'action':f"{len(deep_losers)} lot(s) are down >=20% vs cost basis ({', '.join(r['symbol'] for r in deep_losers[:4])}); re-verify thesis and falsifier rather than anchoring to cost."})
    if overlaps:
        findings.append({'code':'superficial_loss_overlap','priority':'medium','metric':len(overlaps),'action':f"Cross-account or 30-day BUY overlap on {', '.join(overlaps[:5])}; check Canadian 30-day superficial-loss rule before harvesting taxable losses."})
    if rt_7d>0 or rt_30d>=3:
        findings.append({'code':'short_horizon_churn','priority':'medium','metric':{'7d':rt_7d,'30d':rt_30d},'action':f"Detected {rt_7d} <=7d and {rt_30d} <=30d round-trip trades ({', '.join(sorted(churn_syms)[:5])}); review whether short-term turnover added alpha after commissions."})
    if tail_count>=4:
        findings.append({'code':'tail_position_sprawl','priority':'low','metric':tail_count,'action':f"{tail_count} positions are each <1% of NAV; consider consolidating low-conviction tail holdings."})
    if tfsa_wht_syms:
        findings.append({'code':'tfsa_dividend_withholding','priority':'low','metric':sorted(tfsa_wht_syms),'action':f"TFSA incurred unrecoverable US dividend withholding on {', '.join(sorted(tfsa_wht_syms)[:5])}."})
    if untracked:
        findings.append({'code':'missing_decision_journal','priority':'medium','metric':len(untracked),'action':f"{len(untracked)}/{len(open_stk)} open stocks lack a logged thesis/falsifier ({', '.join(untracked[:6])})."})
    top_traded=[{'symbol':k,'trades':v} for k,v in sorted(by_sym.items(), key=lambda x:x[1], reverse=True)[:5]]
    return {
        'status':tax['status'],
        'as_of':snap.get('as_of'),
        'currency':snap.get('currency'),
        'sizing_and_concentration':{'open_positions':len(positions),'cash_weight_of_nav':str(round(cash_total/acct_nav,4)) if acct_nav>0 else None,'top1_weight_of_nav':str(round(top1,4)),'top3_weight_of_nav':str(round(top3,4)),'sub_1pct_tail_positions':tail_count},
        'execution_and_turnover':{'total_trades':trade_count,'fx_spot_conversions':fx_conv_count,'buys':buys,'sells':sells,'distinct_symbols_traded':len(by_sym),'commissions_local_sum':str(round(comms,2)),'round_trips_7d':rt_7d,'round_trips_30d':rt_30d,'most_traded':top_traded},
        'gain_loss_asymmetry':{'winning_lots':win_lots,'losing_lots':loss_lots,'gross_unrealized_gains_base':str(round(gross_gain,2)),'gross_unrealized_losses_base':str(round(gross_loss,2)),'deep_drawdown_lots_20pct':deep_losers[:8]},
        'tax_and_journal_discipline':{'superficial_loss_watch_symbols':overlaps,'taxable_loss_harvest_lots':harvest_count,'tfsa_withholding_symbols':sorted(tfsa_wht_syms),'thesis_coverage_ratio':str(round(coverage,4)),'untracked_symbols':untracked[:12]},
        'findings':findings
    }

def improvement_loop(symbol=None):
    """Recursive self-improvement review: joins deterministic habit telemetry with past mistake post-mortems, improvement goals, case lessons, and live thesis drift."""
    habits=trading_habits()
    sym=str(symbol or '').strip().upper() or None
    tax_map={p['symbol']:p for p in (tax_positions().get('positions') or [])}
    goals=[]; mistakes=[]; lessons=[]; thesis_audit=[]
    for row in knowledge.list_memory(limit=80):
        kind=row['kind']; rsym=str(row.get('symbol') or '').upper()
        if sym and rsym and rsym!=sym: continue
        parsed=None
        try: parsed=json.loads(row['content'])
        except Exception: parsed={'text':row['content']}
        compact={'id':row['id'],'symbol':rsym,'created_at':row['created_at'][:10],'data':parsed}
        if kind=='goal': goals.append(compact)
        elif kind=='mistake': mistakes.append(compact)
        elif kind=='lesson': lessons.append(compact)
        elif kind=='decision':
            tsym=rsym or str((parsed or {}).get('instrument') or '').upper()
            if sym and tsym!=sym: continue
            live=tax_map.get(tsym)
            thesis_audit.append({'id':row['id'],'symbol':tsym,'still_held':bool(live),'weight_of_nav':live.get('weight_of_nav') if live else '0','unrealized_pl_base':live.get('unrealized_pl_base_statement_fx') if live else None,'has_falsifier':bool((parsed or {}).get('risk')),'confidence':(parsed or {}).get('confidence'),'outcome_hit':(parsed or {}).get('outcome_hit'),'observed':bool((parsed or {}).get('observed')),'outcome':(parsed or {}).get('outcome'),'thesis':(parsed or {}).get('thesis'),'falsifier':(parsed or {}).get('risk')})
    conf_vals=[float(t['confidence']) for t in thesis_audit if t.get('confidence') is not None]
    brier_pairs=[(float(t['confidence']), int(t['outcome_hit'])) for t in thesis_audit if t.get('confidence') is not None and t.get('outcome_hit') in (0, 1)]
    brier=round(sum((p-y)**2 for p,y in brier_pairs)/len(brier_pairs), 4) if brier_pairs else None
    calibration={'logged_theses':len(thesis_audit),'with_explicit_falsifier':sum(1 for t in thesis_audit if t['has_falsifier']),'observed_outcomes':sum(1 for t in thesis_audit if t['observed']),'mean_stated_confidence':round(sum(conf_vals)/len(conf_vals),4) if conf_vals else None,'brier_score':brier,'brier_scored_count':len(brier_pairs)}
    next_focus=[]
    for g in goals[:4]:
        d=g['data'] if isinstance(g['data'],dict) else {}
        next_focus.append({'source':'goal','id':g['id'],'rule':d.get('action_rule') or d.get('title') or str(g['data'])[:160]})
    for m in mistakes[:4]:
        d=m['data'] if isinstance(m['data'],dict) else {}
        next_focus.append({'source':'mistake_guardrail','id':m['id'],'symbol':m['symbol'],'rule':d.get('action_rule') or d.get('title') or str(m['data'])[:160]})
    for f in (habits.get('findings') or [])[:5]:
        next_focus.append({'source':'ledger_telemetry','code':f['code'],'priority':f['priority'],'rule':f['action']})
    return {
        'status':habits.get('status','ok'),
        'as_of':habits.get('as_of'),
        'symbol_filter':sym,
        'habit_telemetry':{k:habits.get(k) for k in ('sizing_and_concentration','execution_and_turnover','gain_loss_asymmetry','tax_and_journal_discipline')},
        'active_goals':goals[:10],
        'past_mistakes':mistakes[:10],
        'case_lessons':lessons[:10],
        'thesis_audit':thesis_audit[:15],
        'thesis_calibration':calibration,
        'recursive_next_focus':next_focus
    }

def dcf_sensitivity(fcff, net_debt, shares, waccs, growths):
    grid=[]
    for discount in waccs:
        for terminal in growths:
            try: row=dcf(fcff, discount, terminal, net_debt, shares)
            except ValueError as exc: row={'error':str(exc)}
            grid.append({'wacc':str(dec(discount)),'growth':str(dec(terminal)),'per_share':row.get('per_share'),'error':row.get('error')})
    return {'method':'one_stage_gordon','grid':grid,'price':'unavailable'}

def multi_stage_dcf(fcff, wacc, stage1_growth, terminal_growth, net_debt, shares, years=5, fade=True, fcff_basis='base_t0', symbol='', fetch_live=False, include_grid=True):
    """Multi-stage explicit runway DCF with optional linear growth fade to terminal perpetuity and 3x3 WACC/terminal sensitivity grid."""
    base_cf, discount, g1, g_term, debt, count=dec(fcff), dec(wacc), dec(stage1_growth), dec(terminal_growth), dec(net_debt), dec(shares)
    horizon=int(years)
    if horizon<1 or horizon>20: raise ValueError('years must be between 1 and 20')
    if count<=0 or discount<=0 or discount<=g_term: raise ValueError('shares and WACC minus terminal_growth must be positive')
    schedule=[]; pv_explicit=Decimal(0); cf=base_cf
    for yr in range(1, horizon+1):
        g_t=(g1+(g_term-g1)*Decimal(yr-1)/Decimal(horizon)) if (fade and horizon>1) else g1
        if yr==1 and fcff_basis=='year1': cf=base_cf
        else: cf=cf*(Decimal(1)+g_t)
        df=(Decimal(1)+discount)**yr
        pv=cf/df; pv_explicit+=pv
        schedule.append({'year':yr,'growth_rate':str(round(g_t,5)),'fcff':str(round(cf,4)),'pv_fcff':str(round(pv,4))})
    terminal_cf=cf*(Decimal(1)+g_term)
    terminal_val=terminal_cf/(discount-g_term)
    pv_terminal=terminal_val/((Decimal(1)+discount)**horizon)
    enterprise=pv_explicit+pv_terminal
    equity=enterprise-debt
    per_share=equity/count
    term_share=(pv_terminal/enterprise) if enterprise!=0 else None
    notes=['Explicit multi-stage cash-flow schedule discounted at WACC plus Gordon terminal value at year '+str(horizon)+'.']
    if term_share is not None and term_share>Decimal('0.80'):
        notes.append('Over 80% of Enterprise Value comes from terminal value; scrutinize moat durability and terminal growth.')
    grid=[]
    if include_grid:
        for dw in (Decimal('-0.01'), Decimal('0'), Decimal('0.01')):
            for dg in (Decimal('-0.005'), Decimal('0'), Decimal('0.005')):
                w_i=discount+dw; g_i=g_term+dg
                if w_i<=0 or w_i<=g_i:
                    grid.append({'wacc':str(w_i),'terminal_growth':str(g_i),'per_share':None})
                else:
                    sub=multi_stage_dcf(fcff, str(w_i), stage1_growth, str(g_i), net_debt, shares, years=horizon, fade=fade, fcff_basis=fcff_basis, include_grid=False)
                    grid.append({'wacc':str(w_i),'terminal_growth':str(g_i),'per_share':str(round(dec(sub['per_share']),2))})
    out={'method':'two_stage_fade_dcf' if fade else 'two_stage_constant_dcf','years':horizon,'fcff_basis':fcff_basis,'pv_explicit_fcff':str(round(pv_explicit,4)),'pv_terminal_value':str(round(pv_terminal,4)),'terminal_share_of_ev':str(round(term_share,4)) if term_share is not None else None,'enterprise_value':str(round(enterprise,4)),'equity_value':str(round(equity,4)),'per_share':str(round(per_share,4)),'schedule':schedule,'sensitivity_3x3':grid,'price':'unavailable','price_freshness':'unavailable','upside':'unavailable','limitations':notes}
    return attach_market_price(out, symbol, fetch_live=fetch_live) if str(symbol or '').strip() else out

def reverse_dcf(fcff_t0, wacc, terminal_growth, net_debt, shares, reference_price='', symbol='', years=5, fade=True):
    """Expectations Investing solver: bisection finds the exact Stage-1 FCFF growth rate implied by a reference or cached market price."""
    base_cf=dec(fcff_t0)
    if base_cf<=0:
        return {'status':'nonpositive_base_fcff','limitations':['Reverse DCF requires positive base FCFF (t0). For pre-FCF firms, model explicit revenue and target FCF margin scenarios.']}
    sym=str(symbol or '').strip().upper()
    px=None; basis=None
    if str(reference_price or '').strip():
        px=dec(reference_price); basis='caller_supplied_price'
    elif sym:
        q=market.quote(sym)
        if q.get('freshness')=='live' and q.get('price'):
            px=dec(q['price']); basis='alpaca_iex_live'
        else:
            b=market.bars(sym, 1)
            if b and b[-1].get('close'):
                px=dec(b[-1]['close']); basis='historical_bar_close_'+str(b[-1]['date'])+'_not_live'
    if px is None or px<=0:
        return {'status':'price_unavailable','symbol':sym or None,'limitations':['Supply reference_price or run market-sync so a live quote or dated historical close is available.']}
    def val_at(g):
        res=multi_stage_dcf(str(base_cf), wacc, str(g), terminal_growth, net_debt, shares, years=years, fade=fade, fcff_basis='base_t0', include_grid=False)
        return dec(res['per_share'])-px
    low, high=Decimal('-0.60'), Decimal('1.80')
    v_lo, v_hi=val_at(low), val_at(high)
    benchmarks={}
    for bg in ('0.00','0.05','0.10','0.15','0.25'):
        r=multi_stage_dcf(str(base_cf), wacc, bg, terminal_growth, net_debt, shares, years=years, fade=fade, fcff_basis='base_t0', include_grid=False)
        benchmarks[bg]=str(round(dec(r['per_share']),2))
    if v_lo*v_hi>0:
        return {'status':'out_of_solver_bounds','symbol':sym or None,'reference_price':str(px),'price_basis':basis,'bounds':['-0.60','1.80'],'per_share_at_bounds':[str(round(v_lo+px,2)),str(round(v_hi+px,2))],'per_share_by_stage1_growth':benchmarks,'limitations':['Market price implies Stage-1 growth outside [-60%, +180%] annual range under supplied WACC and share count.']}
    for _ in range(70):
        mid=(low+high)/Decimal(2)
        vm=val_at(mid)
        if abs(vm)<Decimal('0.0001'): low=high=mid; break
        if v_lo*vm<=0: high, v_hi=mid, vm
        else: low, v_lo=mid, vm
    implied=(low+high)/Decimal(2)
    return {
        'status':'ok',
        'method':'reverse_dcf_bisection',
        'symbol':sym or None,
        'reference_price':str(px),
        'price_basis':basis,
        'years':int(years),
        'fade_to_terminal':bool(fade),
        'implied_stage1_fcff_growth':str(round(implied,5)),
        'per_share_by_stage1_growth':benchmarks,
        'limitations':['Implied growth depends strictly on supplied base FCFF, WACC, terminal growth, net debt, and diluted shares. If price_basis is historical_bar_close, it is not a live quote.']
    }

def portfolio_factor_risk(benchmark='QQQ', window=90, account=None):
    """Cross-asset sample covariance, Marginal Contribution to Risk (MCTR), variance attribution, effective bets (HHI & diversification ratio), empirical Beta, and correlation clusters."""
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    tax=tax_positions(account=account)
    if tax.get('status') not in {'ok','partial_or_stale'}: return tax
    acct_rows=[a for a in snap.get('accounts',[]) if not account or a['account']==account]
    acct_nav=float(sum((dec(a['nav']) for a in acct_rows), Decimal(0)))
    if acct_nav<=0: return {'status':'invalid_nav'}
    stk_positions=[p for p in (tax.get('positions') or []) if (p.get('asset') or '').upper()=='STK']
    series_map={}; uncovered=[]
    for p in stk_positions:
        rets=_bar_returns(p['symbol'], max(40, int(window)+10))
        if len(rets)>=20: series_map[p['symbol']]=(float(dec(p['total_value_base']))/acct_nav, rets)
        else: uncovered.append(p['symbol'])
    if not series_map:
        return {'status':'insufficient_bar_history','uncovered_symbols':uncovered,'limitations':['Run market-sync to populate >=20 daily bars for equity holdings.']}
    syms=sorted(series_map.keys())
    common_dates=sorted(set.intersection(*(set(series_map[s][1].keys()) for s in syms)))[-int(window):]
    if len(common_dates)<20:
        return {'status':'insufficient_overlapping_dates','overlapping_days':len(common_dates),'limitations':['Fewer than 20 common trading dates across held symbols.']}
    n_obs=len(common_dates)
    w_nav=[series_map[s][0] for s in syms]
    covered_w=sum(w_nav)
    w_eq=[w/covered_w if covered_w>0 else 0.0 for w in w_nav]
    mat=[[series_map[s][1][d] for d in common_dates] for s in syms]
    means=[sum(row)/n_obs for row in mat]
    cov=[[0.0]*len(syms) for _ in syms]
    for i in range(len(syms)):
        for j in range(i, len(syms)):
            c_ij=sum((mat[i][t]-means[i])*(mat[j][t]-means[j]) for t in range(n_obs))/max(1, n_obs-1)
            cov[i][j]=cov[j][i]=c_ij
    sigma_w_nav=[sum(cov[i][j]*w_nav[j] for j in range(len(syms))) for i in range(len(syms))]
    port_var_daily_nav=max(0.0, sum(w_nav[i]*sigma_w_nav[i] for i in range(len(syms))))
    port_vol_ann_nav=math.sqrt(port_var_daily_nav*252.0)
    sigma_w_eq=[sum(cov[i][j]*w_eq[j] for j in range(len(syms))) for i in range(len(syms))]
    port_var_daily_eq=max(0.0, sum(w_eq[i]*sigma_w_eq[i] for i in range(len(syms))))
    port_vol_ann_eq=math.sqrt(port_var_daily_eq*252.0)
    indiv_vols=[math.sqrt(max(0.0, cov[i][i])*252.0) for i in range(len(syms))]
    weighted_vol_eq=sum(w_eq[i]*indiv_vols[i] for i in range(len(syms)))
    div_ratio=(weighted_vol_eq/port_vol_ann_eq) if port_vol_ann_eq>0 else None
    hhi=sum(w*w for w in w_eq)
    eff_bets=(1.0/hhi) if hhi>0 else None
    bench_sym=str(benchmark or 'QQQ').strip().upper()
    bench_rets=_bar_returns(bench_sym, max(40, int(window)+10))
    bench_common=[d for d in common_dates if d in bench_rets]
    betas={}
    if len(bench_common)>=20:
        bm=[bench_rets[d] for d in bench_common]; b_mean=sum(bm)/len(bm)
        b_var=sum((x-b_mean)**2 for x in bm)/max(1, len(bm)-1)
        if b_var>0:
            for s in syms:
                sr=[series_map[s][1][d] for d in bench_common]; s_mean=sum(sr)/len(sr)
                b_cov=sum((x-s_mean)*(y-b_mean) for x,y in zip(sr,bm))/max(1, len(bm)-1)
                betas[s]=b_cov/b_var
    decomp=[]
    for i, s in enumerate(syms):
        mctr=(sigma_w_nav[i]*252.0/port_vol_ann_nav) if port_vol_ann_nav>0 else 0.0
        var_share=(w_nav[i]*sigma_w_nav[i]/port_var_daily_nav) if port_var_daily_nav>0 else 0.0
        decomp.append({'symbol':s,'weight_of_nav':round(w_nav[i],4),'annualized_vol':round(indiv_vols[i],4),'empirical_beta_vs_'+bench_sym.lower():round(betas[s],4) if s in betas else None,'mctr_annualized':round(mctr,4),'variance_share':round(var_share,4)})
    decomp.sort(key=lambda r:r['variance_share'], reverse=True)
    clusters=[]
    for i in range(len(syms)):
        for j in range(i+1, len(syms)):
            denom=math.sqrt(max(0.0, cov[i][i]*cov[j][j]))
            if denom<=0: continue
            rho=cov[i][j]/denom
            if rho>=0.65:
                clusters.append({'pair':syms[i]+'-'+syms[j],'correlation':round(rho,4),'combined_weight_of_nav':round(w_nav[i]+w_nav[j],4)})
    clusters.sort(key=lambda r:r['correlation'], reverse=True)
    port_beta=sum(w_nav[i]*betas[s] for i,s in enumerate(syms) if s in betas) if betas else None
    return {
        'status':'ok',
        'as_of':snap.get('as_of'),
        'overlapping_trading_days':n_obs,
        'covered_equity_weight_of_nav':round(covered_w,4),
        'uncovered_symbols':uncovered,
        'benchmark_proxy':bench_sym if betas else 'unavailable',
        'benchmark_proxy_basis':'usd_close_price_return_not_cad_total_return',
        'portfolio_metrics':{'annualized_nav_volatility':round(port_vol_ann_nav,4),'annualized_equity_volatility':round(port_vol_ann_eq,4),'diversification_ratio':round(div_ratio,4) if div_ratio else None,'effective_number_of_bets_hhi':round(eff_bets,2) if eff_bets else None,'portfolio_empirical_beta_nav_scaled':round(port_beta,4) if port_beta is not None else None},
        'risk_attribution':decomp,
        'high_correlation_clusters':clusters[:12],
        'limitations':['Covariance, Beta, and MCTR use historical daily close-to-close USD/local returns from cached bars, not intraday ticks or CAD total-return index series.']
    }

def factor_stress_test(benchmark_shock='-0.15', fx_shock='0.02', benchmark='QQQ', account=None):
    """Beta-weighted equity + FX stress scenario using empirical stock Betas to cached benchmark bars."""
    b_shock=dec(benchmark_shock); f_shock=dec(fx_shock)
    if b_shock<=Decimal('-1') or b_shock>Decimal('1') or f_shock<=Decimal('-0.5') or f_shock>Decimal('0.5'):
        raise ValueError('benchmark_shock must be in (-1,1] and fx_shock in (-0.5,0.5]')
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    tax=tax_positions(account=account)
    factor=portfolio_factor_risk(benchmark=benchmark, window=90, account=account)
    beta_key='empirical_beta_vs_'+str(benchmark or 'QQQ').strip().lower()
    beta_map={r['symbol']:Decimal(str(r[beta_key])) for r in (factor.get('risk_attribution') or []) if r.get(beta_key) is not None}
    acct_rows=[a for a in snap.get('accounts',[]) if not account or a['account']==account]
    acct_nav=sum((dec(a['nav']) for a in acct_rows), Decimal(0))
    rows=[]; total_delta=Decimal(0)
    for p in (tax.get('positions') or []):
        val=dec(p['total_value_base']); sym=p['symbol']
        beta=beta_map.get(sym, Decimal('1')); b_src='empirical_90d' if sym in beta_map else 'unit_beta_fallback'
        eq_mult=max(Decimal('0'), Decimal('1')+beta*b_shock)
        fx_mult=(Decimal('1')+f_shock) if (p.get('currency') or '').upper()!=snap['currency'] else Decimal('1')
        stressed=val*eq_mult*fx_mult; delta=stressed-val; total_delta+=delta
        rows.append({'symbol':sym,'current_value_base':str(round(val,2)),'beta_used':str(round(beta,4)),'beta_source':b_src,'fx_applied':fx_mult!=Decimal('1'),'stressed_value_base':str(round(stressed,2)),'pnl_impact_base':str(round(delta,2))})
    rows.sort(key=lambda r:dec(r['pnl_impact_base']))
    return {
        'status':tax['status'],
        'as_of':snap.get('as_of'),
        'currency':snap['currency'],
        'benchmark_proxy':str(benchmark or 'QQQ').upper(),
        'benchmark_shock':str(b_shock),
        'fx_shock_non_base':str(f_shock),
        'portfolio_pnl_impact_base':str(round(total_delta,2)),
        'portfolio_nav_impact_pct':str(round(total_delta/acct_nav,4)) if acct_nav>0 else None,
        'positions':rows,
        'limitations':['Linear empirical-beta + FX stress approximation; options convexity and leveraged-ETF path dependence are not modeled.']
    }

def evaluate_trade_gate(symbol, action, amount_base, account='taxable'):
    """Deterministic 6-point institutional Pre-Trade Gatekeeper & Red-Team audit backed by the canonical IPS evaluate_policy engine."""
    from . import quant
    sym=str(symbol or '').strip().upper()
    act=str(action or '').strip().upper()
    if act not in {'BUY','ADD','TRIM','SELL'}: raise ValueError('action must be BUY, ADD, TRIM, or SELL')
    notional=abs(dec(amount_base))
    if notional<=0: raise ValueError('amount_base must be positive')
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    nav=dec(snap['nav'])
    signed_delta=notional if act in {'BUY','ADD'} else -notional
    tax_all=tax_positions()
    pos_map={p['symbol']:p for p in (tax_all.get('positions') or [])}
    existing=pos_map.get(sym)
    cur_val=dec(existing['total_value_base']) if existing else Decimal(0)
    post_val=max(Decimal(0), cur_val+signed_delta)
    pre_w=cur_val/nav if nav>0 else Decimal(0)
    post_w=post_val/nav if nav>0 else Decimal(0)
    all_vals={s:dec(p['total_value_base']) for s,p in pos_map.items()}
    pre_eq=sum(all_vals.values(), Decimal(0))
    post_vals=dict(all_vals); post_vals[sym]=post_val
    post_eq=sum(post_vals.values(), Decimal(0))
    pre_eff=(Decimal(1)/sum((v/pre_eq)**2 for v in all_vals.values() if v>0)) if pre_eq>0 else None
    post_eff=(Decimal(1)/sum((v/post_eq)**2 for v in post_vals.values() if v>0)) if post_eq>0 else None

    pol_res=quant.evaluate_policy(portfolio_snapshot=snap, proposed_trade={'symbol':sym,'action':act,'amount_base':str(notional),'account':account})
    targets=pol_res.get('policy_targets') or {}
    max_single=Decimal(str(targets.get('max_single_stock_weight', 0.15)))
    max_cluster=Decimal(str(targets.get('max_correlated_cluster_weight', 0.30)))

    sym_rets=_bar_returns(sym, 90); corr_peers=[]; cluster_val=post_val
    if len(sym_rets)>=20:
        for psym, p in pos_map.items():
            if psym==sym or (p.get('asset') or '').upper()!='STK': continue
            rho, _n=_pairwise_corr(sym_rets, _bar_returns(psym, 90))
            if rho is not None and rho>=0.65:
                corr_peers.append({'symbol':psym,'corr_90d':round(rho,4),'weight_of_nav':str(round(dec(p['weight_of_nav']),4))})
                cluster_val+=dec(p['total_value_base'])
        corr_peers.sort(key=lambda r:r['corr_90d'], reverse=True)
    bucket=_tax_bucket(account)
    flags=[]; red_team=[]
    hard_blocks=pol_res.get('hard_blocks') or []
    for hb in hard_blocks:
        flags.append({'gate':hb['rule'],'severity':'block','detail':hb.get('detail') or f"{hb['rule']}: requested {hb.get('requested_sell')} vs available {hb.get('available_position_base')} in {hb.get('account')}"})
    for br in (pol_res.get('breaches') or []):
        if br['rule'] in {'tactical_max_weight_exceeded','max_economic_leverage','max_gross_economic_leverage','min_cash_reserve'}:
            flags.append({'gate':f"policy_{br['rule']}",'severity':'high','detail':f"Post-trade {br['rule']}={br.get('actual')} breaches IPS limit {br.get('limit')}."})
    if act in {'BUY','ADD'} and post_w>=max_single:
        flags.append({'gate':'concentration','severity':'high','detail':f'Post-trade single-name weight reaches {round(post_w*100,2)}% of NAV (>= {round(max_single*100,1)}% IPS limit).'})
        red_team.append(f'What specific catalyst or balance-sheet protection justifies >={round(max_single*100,1)}% single-stock concentration under a -25% drawdown?')
    if act in {'BUY','ADD'} and nav>0 and (cluster_val/nav)>=max_cluster and corr_peers:
        flags.append({'gate':'correlated_cluster','severity':'high','detail':f"Combined correlated cluster ({sym} + {', '.join(r['symbol'] for r in corr_peers[:4])}) reaches {round(cluster_val/nav*100,2)}% of NAV (>= {round(max_cluster*100,1)}% IPS limit)."})
        red_team.append('Are you adding independent alpha, or doubling down on the exact same macro/sector factor already dominating your book?')
    if act in {'SELL','TRIM'} and bucket=='taxable' and existing:
        tax_lot=next((l for l in (existing.get('lots') or []) if l.get('tax_bucket')=='taxable'), None)
        pl=dec(tax_lot['unrealized_pl_base_statement_fx']) if (tax_lot and tax_lot.get('unrealized_pl_base_statement_fx') is not None) else None
        if pl is not None and pl<0 and existing.get('superficial_loss_watch'):
            flags.append({'gate':'canadian_tax_superficial_loss','severity':'high','detail':'Selling at a loss in taxable while holding in TFSA/registered or within 30 days of a BUY risks ITA s.54 superficial-loss denial.'})
        elif pl is not None and pl>0:
            flags.append({'gate':'canadian_tax_capital_gain','severity':'medium','detail':f'Realizes taxable capital gain (statement-FX unrealized P/L {round(pl,2)} {snap["currency"]}).'})
    if act in {'BUY','ADD'} and bucket=='tfsa' and existing:
        has_wht=any(dec(l.get('withholding_tax_local') or '0')!=0 for l in (existing.get('lots') or []))
        if has_wht:
            flags.append({'gate':'tfsa_withholding_drag','severity':'low','detail':'US dividend payer in TFSA faces unrecoverable 15% IRS withholding.'})
    decisions=[r for r in knowledge.list_memory('decision', limit=100) if str(r.get('symbol') or '').upper()==sym]
    has_falsifier=False
    for d in decisions:
        try:
            if json.loads(d['content']).get('risk'): has_falsifier=True
        except Exception:
            pass
    if act in {'BUY','ADD'} and not has_falsifier:
        flags.append({'gate':'missing_thesis_falsifier','severity':'medium','detail':f'No logged decision thesis with an explicit exit falsifier found for {sym}.'})
        red_team.append(f'State the exact measurable fundamental falsifier that would prove this {sym} thesis wrong before committing capital.')
    guards=knowledge.memory_guardrails(sym, 5)
    if guards:
        flags.append({'gate':'past_mistake_and_goal_guardrails','severity':'medium','detail':f'{len(guards)} active personal rule(s)/post-mortem(s) apply to this decision.'})
    if not red_team:
        red_team.append(f'What does the market currently misprice in {sym} (check reverse_dcf implied growth), and why is your counterparty selling/buying here?')
    if not snap.get('advice_allowed'):
        verdict='blocked_stale_ledger'
    elif hard_blocks:
        verdict='blocked_policy_violation'
    elif flags:
        verdict='caution_review_flags'
    else:
        verdict='clear'
    return {
        'verdict':verdict,
        'symbol':sym,
        'action':act,
        'account':account,
        'tax_bucket':bucket,
        'amount_base':str(round(notional,2)),
        'portfolio_as_of':snap.get('as_of'),
        'portfolio_nav':snap.get('nav'),
        'policy_hash':pol_res.get('policy_hash'),
        'post_trade_policy_exposure':pol_res.get('exposure_summary'),
        'sizing_shift':{'pre_weight_of_nav':str(round(pre_w,4)),'post_weight_of_nav':str(round(post_w,4)),'effective_bets_before':str(round(pre_eff,2)) if pre_eff else None,'effective_bets_after':str(round(post_eff,2)) if post_eff else None,'correlated_cluster_weight_after':str(round(cluster_val/nav,4)) if nav>0 else None},
        'correlated_peers_65pct':corr_peers[:6],
        'gate_flags':flags,
        'active_guardrails':guards,
        'red_team_questions':red_team,
        'orders':'disabled'
    }

def filing_red_flag_audit(symbol, include_private=True):
    """Forensic SEC 10-K/20-F audit combining multi-year XBRL accrual/dilution quality checks with paragraph-level Risk Factor diffs (~95% boilerplate stripped)."""
    sym=str(symbol or '').strip().upper()
    if not sym: raise ValueError('symbol is required')
    xbrl_rows=market.filings(sym)
    by_period={}
    for r in xbrl_rows:
        by_period.setdefault(r['period'], {})[r['concept']]=dec(r['value'])
    periods=sorted(by_period.keys(), reverse=True)
    forensic_metrics={}; red_flags=[]
    if periods:
        cur=by_period[periods[0]]
        rev=cur.get('RevenueFromContractWithCustomerExcludingAssessedTax') or cur.get('Revenues') or cur.get('SalesRevenueNet')
        ni=cur.get('NetIncomeLoss')
        ocf=cur.get('NetCashProvidedByUsedInOperatingActivities')
        capex=cur.get('PaymentsToAcquirePropertyPlantAndEquipment')
        fcf=cur.get('DerivedFreeCashFlow')
        cash=cur.get('CashAndCashEquivalentsAtCarryingValue') or Decimal(0)
        debt=(cur.get('LongTermDebt') or cur.get('LongTermDebtNoncurrent') or Decimal(0))+(cur.get('ShortTermBorrowings') or Decimal(0))
        sh=cur.get('WeightedAverageNumberOfDilutedSharesOutstanding') or cur.get('CommonStockSharesOutstanding')
        cash_conv=(ocf/ni) if (ocf is not None and ni is not None and ni>0) else None
        capex_intensity=(abs(capex)/ocf) if (capex is not None and ocf is not None and ocf>0) else None
        forensic_metrics={
            'latest_period':periods[0],
            'revenue':str(rev) if rev is not None else None,
            'net_income':str(ni) if ni is not None else None,
            'operating_cash_flow':str(ocf) if ocf is not None else None,
            'derived_fcf':str(fcf) if fcf is not None else None,
            'net_debt_approx':str(debt-cash) if (debt or cash) else None,
            'diluted_shares':str(sh) if sh is not None else None,
            'ocf_to_net_income_ratio':str(round(cash_conv,4)) if cash_conv is not None else None,
            'capex_to_ocf_ratio':str(round(capex_intensity,4)) if capex_intensity is not None else None
        }
        if cash_conv is not None and cash_conv<Decimal('0.75'):
            red_flags.append({'source':'xbrl_forensics','code':'low_cash_conversion','severity':'high','detail':f'Operating cash flow is only {round(cash_conv*100,1)}% of GAAP Net Income in {periods[0]} (<75% accrual quality threshold).'})
        if ni is not None and ni>0 and ocf is not None and ocf<=0:
            red_flags.append({'source':'xbrl_forensics','code':'positive_ni_negative_ocf','severity':'high','detail':f'Positive GAAP Net Income with non-positive Operating Cash Flow in {periods[0]}.'})
        if len(periods)>=2:
            prev=by_period[periods[1]]
            ni_prev=prev.get('NetIncomeLoss'); ocf_prev=prev.get('NetCashProvidedByUsedInOperatingActivities')
            sh_prev=prev.get('WeightedAverageNumberOfDilutedSharesOutstanding') or prev.get('CommonStockSharesOutstanding')
            if ni is not None and ni_prev is not None and ocf is not None and ocf_prev is not None:
                if ni>ni_prev and ocf<ocf_prev:
                    red_flags.append({'source':'xbrl_forensics','code':'accrual_yoy_divergence','severity':'high','detail':f'Net Income rose YoY ({periods[1]} -> {periods[0]}) while Operating Cash Flow declined YoY.'})
            if sh is not None and sh_prev is not None and sh_prev>0:
                dil=(sh-sh_prev)/sh_prev
                forensic_metrics['share_count_change_yoy']=str(round(dil,4))
                if dil>Decimal('0.03'):
                    red_flags.append({'source':'xbrl_forensics','code':'share_dilution_above_3pct','severity':'medium','detail':f'Diluted share count expanded {round(dil*100,2)}% YoY.'})
    rf_diff=market.diff_filing_sections(sym, 'risk_factors', max_items=6)
    if rf_diff.get('status')=='ok':
        for esc in rf_diff.get('modal_escalations_hypothetical_to_realized') or []:
            red_flags.append({'source':'10k_item1a_diff','code':'risk_modal_escalation','severity':'high','detail':'Risk wording shifted from hypothetical (may/could) to realized (has/have experienced): '+esc['latest_excerpt'][:180]})
        flagged_adds=[a for a in (rf_diff.get('newly_added_paragraphs') or []) if a.get('red_flag_terms')]
        if flagged_adds:
            red_flags.append({'source':'10k_item1a_diff','code':'new_risk_factor_disclosures','severity':'medium','detail':f"{len(flagged_adds)} newly added Risk Factor paragraph(s) contain red-flag terms ({', '.join(flagged_adds[0]['red_flag_terms'][:4])})."})
    held_ctx=None
    if include_private:
        tax=tax_positions(symbol=sym)
        pos=(tax.get('positions') or [])
        held_ctx={'held':bool(pos),'weight_of_nav':pos[0].get('weight_of_nav') if pos else '0','active_guardrails':knowledge.memory_guardrails(sym, 4)}
    return {
        'symbol':sym,
        'status':'ok' if (periods or rf_diff.get('status')=='ok') else 'awaiting_edgar_sync',
        'xbrl_forensics':forensic_metrics or None,
        'risk_factor_delta':rf_diff,
        'red_flags':red_flags,
        'portfolio_context':held_ctx,
        'limitations':['XBRL ratios and paragraph diffs come strictly from cached SEC EDGAR 10-K/20-F filings. Set market.edgar_contact and run market-sync to populate uncached symbols.']
    }

def counterfactual_attribution(days=90, account=None):
    """What-If Static Buy-and-Hold Ledger Replay: compares actual ending wealth against holding the start-of-window portfolio untouched (zero trades)."""
    lookback=max(1, min(int(days), 3650))
    snap, blocked=_ready()
    if blocked and snap.get('status') not in {'ok','partial_or_stale'}: return blocked
    as_of_dt=date.fromisoformat(snap['as_of'])
    cutoff_dt=as_of_dt-timedelta(days=lookback)
    cutoff_iso=cutoff_dt.isoformat()
    base_cur=snap['currency']
    acct_rows=[a for a in snap.get('accounts',[]) if not account or a['account']==account]
    actual_end_nav=sum((dec(a['nav']) for a in acct_rows), Decimal(0))
    fx_row=market.fx('FXUSDCAD')
    fallback_fx=dec(fx_row['rate']) if fx_row.get('rate') else Decimal('1')
    end_mark_by_conid={}; end_mark_by_sym={}
    for p in snap.get('positions') or []:
        qty=dec(p['quantity'])
        if qty!=0:
            unit_base=abs(dec(p['value_base'])/qty)
            end_mark_by_conid[str(p['conid'])]=(unit_base, 'statement_open_position')
            end_mark_by_sym[str(p['symbol']).upper()]=(unit_base, 'statement_open_position')
    trades=[]
    with db('finance') as c:
        finance_schema(c)
        q="SELECT account, record_id, payload FROM records WHERE category='trade'"+(" AND account=?" if account else "")
        for row in c.execute(q, (account,) if account else ()):
            f=json.loads(row['payload'])
            raw_dt=f.get('tradeDate') or f.get('reportDate') or f.get('dateTime') or ''
            if not raw_dt: continue
            try: t_iso=day(raw_dt)
            except Exception: continue
            if cutoff_iso<t_iso<=snap['as_of']:
                trades.append((t_iso, row['account'], row['record_id'], f))
    trades.sort(key=lambda x:x[0])
    buy_alpha=Decimal(0); sell_alpha=Decimal(0); comm_drag=Decimal(0); fx_spot_skipped=0
    premature_sell_cost=Decimal(0); protective_sell_gain=Decimal(0)
    scored_trades=[]; unpriced=[]
    for t_iso, acct, rid, f in trades:
        asset=str(f.get('assetCategory') or 'STK').upper()
        if asset=='CASH':
            fx_spot_skipped+=1; continue
        side=str(f.get('buySell') or '').strip().upper()
        if side not in {'BUY','SELL'}: continue
        cid=str(f.get('conid') or '')
        sym=str(f.get('symbol') or cid).upper()
        cur=str(f.get('currency') or base_cur).upper()
        mult=dec(f.get('multiplier') or '1')
        abs_qty=abs(dec(f.get('quantity') or '0'))
        if abs_qty==0 or f.get('tradePrice') in (None,''): continue
        signed_qty=abs_qty if side=='BUY' else -abs_qty
        t_fx=Decimal('1') if cur==base_cur else dec(f.get('fxRateToBase') or fallback_fx)
        exec_unit_base=dec(f['tradePrice'])*t_fx*mult
        comm_cur=str(f.get('ibCommissionCurrency') or cur).upper()
        comm_fx=Decimal('1') if comm_cur==base_cur else t_fx
        comm_base=abs(dec(f.get('ibCommission') or '0'))*comm_fx
        mark_info=end_mark_by_conid.get(cid) or end_mark_by_sym.get(sym)
        if mark_info is None and asset=='STK':
            b_rows=market.bars(sym, 120)
            valid_b=[b for b in b_rows if b['date']<=snap['as_of']]
            if valid_b:
                b_fx=Decimal('1') if (valid_b[-1].get('currency') or 'USD').upper()==base_cur else fallback_fx
                mark_info=(dec(valid_b[-1]['close'])*b_fx, 'cached_historical_bar_'+valid_b[-1]['date'])
        if mark_info is None:
            unpriced.append({'date':t_iso,'symbol':sym,'side':side,'asset':asset,'record_id':rid})
            continue
        end_unit_base, mark_src=mark_info
        alpha=signed_qty*(end_unit_base-exec_unit_base)-comm_base
        comm_drag+=comm_base
        if side=='BUY': buy_alpha+=alpha
        else:
            sell_alpha+=alpha
            if alpha<0: premature_sell_cost+=alpha
            elif alpha>0: protective_sell_gain+=alpha
        scored_trades.append({
            'date':t_iso,
            'account':acct,
            'symbol':sym,
            'side':side,
            'quantity':str(round(abs_qty,4)),
            'exec_unit_base':str(round(exec_unit_base,4)),
            'end_unit_base':str(round(end_unit_base,4)),
            'commission_base':str(round(comm_base,2)),
            'decision_alpha_base':str(round(alpha,2)),
            'mark_source':mark_src
        })
    net_alpha=buy_alpha+sell_alpha
    static_nav=actual_end_nav-net_alpha
    scored_sorted=sorted(scored_trades, key=lambda r:dec(r['decision_alpha_base']), reverse=True)
    verdict='active_trading_added_value' if net_alpha>0 else ('active_trading_detracted_vs_buy_and_hold' if net_alpha<0 else 'neutral_no_active_delta')
    return {
        'status':snap['status'],
        'window':{'from':cutoff_iso,'to':snap['as_of'],'days':lookback},
        'currency':base_cur,
        'verdict':verdict,
        'wealth_comparison':{
            'actual_ending_nav_base':str(round(actual_end_nav,2)),
            'counterfactual_static_buy_and_hold_nav_base':str(round(static_nav,2)),
            'net_active_trading_alpha_base':str(round(net_alpha,2)),
            'active_alpha_pct_of_static_nav':str(round(net_alpha/static_nav,4)) if static_nav>0 else None
        },
        'attribution_breakdown':{
            'scored_trades_count':len(scored_trades),
            'fx_spot_conversions_excluded':fx_spot_skipped,
            'unpriced_closed_trades_count':len(unpriced),
            'buy_entry_timing_alpha_base':str(round(buy_alpha,2)),
            'sell_exit_discipline_alpha_base':str(round(sell_alpha,2)),
            'premature_winner_exits_foregone_upside_base':str(round(premature_sell_cost,2)),
            'protective_exits_drawdown_saved_base':str(round(protective_sell_gain,2)),
            'commission_friction_drag_base':str(round(comm_drag,2))
        },
        'best_decisions':scored_sorted[:5],
        'costliest_decisions':list(reversed(scored_sorted[-5:])) if scored_sorted else [],
        'unpriced_closed_trades':unpriced[:10],
        'limitations':['Counterfactual replay holds start-of-window share counts untouched to as_of and values closed-out stocks using statement positions or cached historical closes; interim dividends on exited shares and cash interest reinvestment are excluded.']
    }

def compute_canadian_acb(symbol=None, account=None):
    """v0.4.1 R04: Pooled Adjusted Cost Base (ACB) by identical property from trade/corporate_action records with transaction FX and commissions."""
    sym_filter=str(symbol or '').strip().upper() or None
    cfg=config().get('finance',{})
    base_cur=str(cfg.get('base_currency') or 'CAD').upper()
    with db('finance') as c:
        finance_schema(c)
        q="SELECT account, category, record_id, payload FROM records WHERE category IN ('trade','corporate_action')"+(" AND account=?" if account else "")+" ORDER BY rowid ASC"
        rows=c.execute(q, (account,) if account else ()).fetchall()
    pools={}
    realized=[]
    for r in rows:
        f=json.loads(r['payload'])
        sym=str(f.get('symbol') or '').strip().upper()
        if not sym or (sym_filter and sym!=sym_filter): continue
        if str(f.get('assetCategory') or 'STK').upper()=='CASH': continue
        bucket=_tax_bucket(r['account'])
        key=(bucket, sym)
        pool=pools.setdefault(key, {'shares':Decimal(0),'total_acb_base':Decimal(0)})
        if r['category']=='trade':
            side=str(f.get('buySell') or '').strip().upper()
            qty=abs(dec(f.get('quantity') or '0'))
            if qty==0 or f.get('tradePrice') in (None,''): continue
            cur=str(f.get('currency') or base_cur).upper()
            fx=Decimal('1') if cur==base_cur else dec(f.get('fxRateToBase') or '1')
            mult=dec(f.get('multiplier') or '1')
            gross_base=qty*dec(f['tradePrice'])*mult*fx
            comm_base=abs(dec(f.get('ibCommission') or '0'))*(Decimal('1') if str(f.get('ibCommissionCurrency') or cur).upper()==base_cur else fx)
            if side=='BUY':
                pool['shares']+=qty
                pool['total_acb_base']+=(gross_base+comm_base)
            elif side=='SELL' and pool['shares']>0:
                sell_qty=min(qty, pool['shares'])
                acb_per_sh=pool['total_acb_base']/pool['shares']
                acb_relieved=acb_per_sh*sell_qty
                proceeds=gross_base-comm_base
                gain=proceeds-acb_relieved
                pool['shares']-=sell_qty
                pool['total_acb_base']-=acb_relieved
                realized.append({'tax_bucket':bucket,'symbol':sym,'sold_qty':str(sell_qty),'proceeds_base':str(round(proceeds,2)),'acb_relieved_base':str(round(acb_relieved,2)),'realized_gain_base':str(round(gain,2))})
    summary=[]
    for (bucket, sym), pool in sorted(pools.items()):
        if pool['shares']==0 and not any(x['symbol']==sym and x['tax_bucket']==bucket for x in realized): continue
        acb_unit=(pool['total_acb_base']/pool['shares']) if pool['shares']>0 else Decimal(0)
        summary.append({'tax_bucket':bucket,'symbol':sym,'shares_held':str(pool['shares']),'pooled_acb_base':str(round(pool['total_acb_base'],2)),'acb_per_unit_base':str(round(acb_unit,4))})
    return {'status':'ok','base_currency':base_cur,'acb_pools':summary,'realized_dispositions':realized,'limitations':['Educational pooled-ACB estimate from recorded trades; verify superficial-loss adjustments before tax filing.']}

def discover_opportunities(universe_symbols=None, features_by_symbol=None, max_candidates=5):
    """v0.6.0 A01: Two-stage deterministic opportunity screener (no daily whole-market LLM scan; returns no_compelling_opportunity when thresholds fail)."""
    syms=[str(s).strip().upper() for s in (universe_symbols or market.universe(include_private=False)) if str(s).strip()]
    feat_map={k.upper():v for k,v in (features_by_symbol or {}).items()}
    scored=[]; excluded=[]
    for sym in syms:
        f=feat_map.get(sym)
        if not f:
            b=market.bars(sym, 60)
            if len(b)<20:
                excluded.append({'symbol':sym,'reason':'insufficient_fundamental_and_price_features'}); continue
            px0=float(b[0]['close']); px1=float(b[-1]['close']); dd=(px1/max(px0,1e-6))-1.0
            excluded.append({'symbol':sym,'reason':'price_only_without_fundamental_quality_features','return_60d':round(dd,4)}); continue
        roic=float(f.get('roic') or 0.0); fcf_g=float(f.get('fcf_growth') or 0.0)
        disc=float(f.get('valuation_discount_pct') or 0.0); dil=float(f.get('dilution_rate') or 0.0)
        if roic<0.10 or fcf_g<0.0 or dil>0.05:
            excluded.append({'symbol':sym,'reason':'failed_quality_or_dilution_floor','roic':roic,'fcf_growth':fcf_g,'dilution':dil}); continue
        if disc<0.15:
            excluded.append({'symbol':sym,'reason':'insufficient_margin_of_safety','valuation_discount_pct':disc}); continue
        score=round(0.40*min(1.0, roic/0.25)+0.35*min(1.0, disc/0.40)+0.25*min(1.0, fcf_g/0.20), 4)
        scored.append({'symbol':sym,'composite_score':score,'roic':roic,'fcf_growth':fcf_g,'valuation_discount_pct':disc,'dilution_rate':dil})
    scored.sort(key=lambda r:r['composite_score'], reverse=True)
    top=scored[:max(1, int(max_candidates))]
    return {'status':'ok' if top else 'no_compelling_opportunity','screened_count':len(syms),'qualified_count':len(scored),'candidates':top,'excluded':excluded,'limitations':['Stage A deterministic screen only; candidates require Stage B primary evidence and Red-Team verification before any proposal.']}

def daily_pnl_bridge(days=30):
    """v0.7.0 Q03: Portfolio NAV change decomposition into market price P&L, external cashflows, dividends/interest, commissions, and residual."""
    snap=finance.load_full_portfolio_snapshot()
    if snap.status in {'unavailable','not_configured','missing_accounts','date_mismatch'}:
        return {'status':snap.status}
    perf=finance.performance()
    intervals=perf.get('intervals') or []
    if not intervals: return {'status':'insufficient_nav_history','as_of':snap.as_of}
    win=intervals[-max(1,int(days)):]
    ext_flows=sum((dec(r['external_flow']) for r in win), Decimal(0))
    cutoff=(date.fromisoformat(snap.as_of)-timedelta(days=int(days))).isoformat()
    divs=Decimal(0); comms=Decimal(0)
    with db('finance') as c:
        finance_schema(c)
        for r in c.execute("SELECT category, payload FROM records WHERE category IN ('cash','trade')"):
            f=json.loads(r['payload'])
            raw_d=f.get('reportDate') or f.get('tradeDate') or f.get('dateTime') or ''
            if not raw_d: continue
            try: d_iso=day(raw_d)
            except Exception: continue
            if d_iso<=cutoff or d_iso>snap.as_of: continue
            if r['category']=='cash':
                kind=str(f.get('type') or '').lower()
                if 'dividend' in kind or 'interest' in kind:
                    divs+=dec(f.get('amountInBase') or f.get('amount') or 0)
            elif r['category']=='trade':
                comms+=abs(dec(f.get('ibCommission') or 0))
    return {'status':'ok','window_days':int(days),'as_of':snap.as_of,'ending_nav_base':str(snap.nav),'external_flows_base':str(round(ext_flows,2)),'dividends_and_interest_base':str(round(divs,2)),'commissions_base':str(round(comms,2)),'limitations':['Residual market P&L and FX translation are inferred after subtracting external flows, income, and commissions.']}
