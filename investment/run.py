"""One entry point; real delivery needs explicit paired approval arguments."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def main():
 parser=argparse.ArgumentParser(description='养鹅只读研究：无券商下单，自动任务须单独批准')
 mode=parser.add_mutually_exclusive_group()
 mode.add_argument('--generate',action='store_true',help='现在生成报告，不推送')
 mode.add_argument('--daily',action='store_true',help='交易日14:38–14:44流程；默认仅预览通知')
 mode.add_argument('--daily-plan',action='store_true',help='核对自动流程计划，不取行情、不发通知')
 parser.add_argument('--confirm-bark-send',action='store_true',help='仅在取得明确授权后与--daily和配置路径一起使用')
 parser.add_argument('--bark-config',type=Path)
 parser.add_argument('--port',type=int,default=9394)
 args=parser.parse_args()
 if (args.confirm_bark_send or args.bark_config) and not(args.daily and args.confirm_bark_send and args.bark_config):parser.error('发送必须同时明确--daily、--confirm-bark-send及--bark-config；计划模式不能发送。')
 if args.daily or args.daily_plan:
  from investment.daily import run_daily
  result=run_daily(ROOT,planning=args.daily_plan,confirmed_send=args.confirm_bark_send,config_path=args.bark_config)
  print(json.dumps(result,ensure_ascii=True));return
 if args.generate:
  from investment.engine import run_analysis
  report=run_analysis(ROOT);print(json.dumps({'id':report['id'],'asOf':report['asOf'],'status':report['status'],'notificationSent':False}));return
 from investment.control import open_control
 open_control(ROOT,args.port)
if __name__=='__main__':main()
