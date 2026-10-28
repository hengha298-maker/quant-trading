"""One entry point: local control window, or explicit one-shot report generation."""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def main():
 p=argparse.ArgumentParser(description='养鹅只读投资研究：无下单、无自动任务、无隐式Bark')
 p.add_argument('--generate',action='store_true',help='只生成一份独立报告，不推送')
 p.add_argument('--port',type=int,default=9394)
 a=p.parse_args()
 if a.generate:
  from investment.engine import run_analysis
  r=run_analysis(ROOT);print(json.dumps({'id':r['id'],'asOf':r['asOf'],'status':r['status'],'notificationSent':False}));return
 from investment.control import open_control
 open_control(ROOT,a.port)
if __name__=='__main__':main()
