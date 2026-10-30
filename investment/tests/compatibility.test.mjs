import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {saveReport, reportAssessment} from '../../v3/web/planning.js';
import {emptyState} from '../../v3/web/domain.js';
test('Python export really imports into the unchanged installed V3 app',()=>{
 const root=fileURLToPath(new URL('../../',import.meta.url));
 const py=process.env.GOOSE_RESEARCH_PYTHON||path.resolve(root,'../Runtime/Research32/Scripts/python.exe');
 const code="import sys,json;sys.path.insert(0,sys.argv[1]);from investment.engine import build_analysis,compatible_report;print(json.dumps(compatible_report(build_analysis({'quotes':{},'histories':{}},None))))";
 const r=spawnSync(py,['-I','-B','-c',code,root],{encoding:'utf8'});
 assert.equal(r.status,0,r.stderr);const report=JSON.parse(r.stdout);assert.match(report.generatedAt,/Z$/);
 const state=saveReport(emptyState(),report);assert.equal(state.planning.reports.length,1);
 assert.equal(state.planning.reports[0].backtest,null);assert.deepEqual(state.planning.reports[0].paperTrades,[]);
});
