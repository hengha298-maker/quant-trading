import json
import sys
from quant_paper.cli import main


def test_cli_status_and_restore(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'initial_cash':1234}))
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'status'])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['cash_fen'] == 123400
    (tmp_path/'config.json').write_text(json.dumps({'initial_cash':9999}))
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['cash_fen'] == 123400


def test_cli_rejects_live(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'config.json').write_text(json.dumps({'mode':'live'}))
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'run'])
    assert main() == 1
    assert 'ValueError' in (tmp_path/'logs/paper.log').read_text()
    assert not (tmp_path/'data/paper.sqlite3').exists()
