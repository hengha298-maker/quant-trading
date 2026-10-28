import test from 'node:test';import assert from 'node:assert/strict';
import {decimalDisplay,moneyDisplay,percentDisplay,bpsDisplay,instrumentLabel} from '../web/presentation.js';
test('exact two-decimal ratios never leak long strings',()=>{assert.equal(percentDisplay('40.1234567890123456789'),'40.12%');assert.equal(percentDisplay('99.999999'),'100.00%');assert.equal(percentDisplay('0'),'0.00%');});
test('money rounds symmetrically and keeps zero and unknown distinct',()=>{assert.equal(moneyDisplay('0'),'¥ 0.00');assert.equal(moneyDisplay(null),'未知');assert.equal(moneyDisplay('-1.005'),'¥ -1.01');assert.equal(decimalDisplay('999999999999999999999.999'),'1000000000000000000000.00');});
test('prices and costs retain explicit precision',()=>{assert.equal(decimalDisplay('1.449',3),'1.449');assert.equal(decimalDisplay('29.8123456',6),'29.812346');assert.equal(bpsDisplay(-1234),'-12.34%');});
test('instrument code is shown only once',()=>{assert.equal(instrumentLabel('600900.SH 长江电力 600900','600900.SH'),'长江电力 · 600900.SH');assert.equal(instrumentLabel('515450.SH','515450.SH'),'515450.SH');assert.equal(instrumentLabel('红利低波ETF','515450.SH'),'红利低波ETF · 515450.SH');});
