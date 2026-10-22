import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutFor,nextSelection,presentDomains,selectionAfterRemoval,fixtures} from './model.mjs';
test('local/remote swapping keeps identical total bounds and lower panel size',()=>{
 for(const width of [296,320,366,390,430,720,1000,1440,1920])for(const stage of [0,1,2])for(const count of [1,2,5,20]){
  const a=layoutFor(width,count,stage,false),b=layoutFor(width,count,stage,true);
  assert.equal(a.total,b.total);assert.equal(a.left,b.left);assert.equal(a.detailHeight,b.detailHeight);
  assert.equal(a.local,b.remote);assert.equal(a.remote,b.local);
  assert.equal(a.local+a.remote+a.gap,a.headerWidth);assert.equal(b.local+b.remote+b.gap,b.headerWidth);
  assert.ok(a.left>=16);assert.ok(a.left+a.total<=width-16);
 }
});

test('remote header matches the detail width at every stage; local-only remains connected',()=>{
 for(const width of [320,390,1440])for(const stage of [0,1,2]){
  const remote=layoutFor(width,2,stage),local=layoutFor(width,0,stage);
  assert.equal(remote.headerWidth,Math.min(420,width-32));
  assert.equal(remote.headerWidth,remote.total);
  assert.equal(remote.detailGap,stage ? 12 : 0);
  assert.equal(remote.detailTop,stage ? 74 : 62);
  assert.equal(remote.layout,'remote-split');
  assert.equal(local.headerWidth,local.total);
  assert.equal(local.detailGap,0);
  assert.equal(local.layout,'local-connected');
  assert.equal(remote.detailHeight,local.detailHeight);
 }
});
test('native stage dimensions are preserved and no external entries add width',()=>{
 assert.equal(layoutFor(1440,5).total,420);
 assert.equal(layoutFor(1440,5).long,348);
 assert.equal(layoutFor(1440,5,1).total,420);
 assert.equal(layoutFor(1440,5,1).detailHeight+62,244);
 assert.equal(layoutFor(1440,5,2).detailHeight+62,580);
 assert.equal(layoutFor(1440,0).local,300);
});
test('inactive circle switches origin without changing stage; active capsule toggles',()=>{
 for(const stage of [0,1,2])assert.deepEqual(nextSelection('local','laptop',stage),{selected:'laptop',stage});
 assert.deepEqual(nextSelection('laptop','laptop',0),{selected:'laptop',stage:1});
 assert.deepEqual(nextSelection('laptop','laptop',2),{selected:'laptop',stage:0});
});
test('filtering and source removal retain stable identities',()=>{
 assert.deepEqual(presentDomains([...fixtures.mixed.domains,{id:'empty',count:0}],new Set(['build'])).map(d=>d.id),['laptop']);
 assert.equal(selectionAfterRemoval('laptop',[]),'local');
 assert.equal(selectionAfterRemoval('laptop',fixtures.mixed.domains),'laptop');
});
