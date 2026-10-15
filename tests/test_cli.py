import json
import sys
from quant_paper.cli import main


def test_cli_status_and_restore(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'initial_cash':1234}), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'status'])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['cash_fen'] == 123400
    (tmp_path/'config.json').write_text(json.dumps({'initial_cash':9999}), encoding='utf-8')
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['cash_fen'] == 123400


def test_cli_rejects_live(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'mode':'live'}), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'run'])
    assert main() == 1
    log = (tmp_path/'logs/paper.log').read_text(encoding='utf-8')
    assert 'ValueError' in log
    assert '运行失败，禁止继续下单' in log
    assert '仅支持 paper，禁止实盘' in log
    assert not (tmp_path/'data/paper.sqlite3').exists()
