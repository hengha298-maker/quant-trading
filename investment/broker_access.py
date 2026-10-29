"""Evidence registry, not an account connection or eligibility assertion."""
def status():
 return {'reviewedOn':'2026-10-29','accountConnected':False,'liveExecutionEnabled':False,
  'brokers':[
   {'broker':'华宝证券','eligibility':'本人账户权限未确认','officialEvidence':'官网列有网上及机构客户端；尚未核实个人账户只读API、QMT/MiniQMT/PTrade支持与资金门槛。','url':'https://www.cnhbstock.com/download/83','contact':'4008209898／所属营业部'},
   {'broker':'银河证券','eligibility':'本人账户权限未确认','officialEvidence':'官网列有星耀数智和PB产品；产品存在不证明个人账户可申请查询接口或已经开通。','url':'https://www.chinastock.com.cn/newsite/online/downloadCenter.html','contact':'95551／所属营业部'}],
  'technicalEvidence':{'description':'迅投官方文档有资产、持仓、成交与委托查询；这不是两家券商对本账户的授权证明。','url':'https://dict.thinktrader.net/nativeApi/xttrader.html'},
  'questionToBroker':'本人已有普通证券账户，仅希望本机只读查询持仓、可卖数量、资金、成交、委托状态和分红税费。是否有官方API或支持的QMT/MiniQMT/PTrade查询权限？是否可禁止下单/撤单？需要哪些资产、适当性、费用和申请材料？若不能，请提供持仓、交割单和资金流水的CSV/Excel导出方式。',
  'currentFallback':'分券商持仓确认＋CSV/XLSX成交增量导入、去重、撤销和数量对账；不存券商密码，不模拟点击登录。'}
