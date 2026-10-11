import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source=readFileSync(new URL('../site/theme.js',import.meta.url),'utf8');
function browser({stored=null,dark=false,blocked=false}={}){
  const root={dataset:{},style:{}},listeners={},events={},media={matches:dark,addEventListener:(name,fn)=>{events.media=fn}};
  const buttons=['light','dark','system'].map(mode=>({dataset:{themeChoice:mode},attributes:{},setAttribute(name,value){this.attributes[name]=value},addEventListener(name,fn){this.click=fn}}));
  let saved=stored;
  runInNewContext(source,{
    document:{documentElement:root,querySelectorAll:()=>buttons,addEventListener:(name,fn)=>{listeners[name]=fn}},
    window:{matchMedia:()=>media,addEventListener:(name,fn)=>{events[name]=fn}},
    localStorage:{getItem(){if(blocked)throw Error('Blocked');return saved},setItem(key,value){if(blocked)throw Error('Blocked');saved=value}}
  });
  return {root,buttons,ready:()=>listeners.DOMContentLoaded(),choose:mode=>buttons.find(b=>b.dataset.themeChoice===mode).click(),system(value){media.matches=value;events.media()},storage:value=>events.storage({key:'datawork-theme',newValue:value}),saved:()=>saved};
}
test('first paint follows system, then responds to OS changes',()=>{
  const b=browser({dark:true});assert.equal(b.root.dataset.theme,'dark');b.ready();assert.equal(b.buttons[2].attributes['aria-pressed'],'true');b.system(false);assert.equal(b.root.dataset.theme,'light');
});
test('saved choice overrides OS before first paint and after OS changes',()=>{
  const b=browser({stored:'light',dark:true});assert.equal(b.root.dataset.theme,'light');b.ready();b.system(true);assert.equal(b.root.dataset.theme,'light');assert.equal(b.root.style.colorScheme,'light');
});
test('theme selection persists and switching to system re-enables OS following',()=>{
  const b=browser();b.ready();b.choose('dark');assert.equal(b.saved(),'dark');assert.equal(b.root.dataset.theme,'dark');assert.equal(b.buttons[1].attributes['aria-pressed'],'true');b.choose('system');assert.equal(b.root.dataset.theme,'light');b.system(true);assert.equal(b.root.dataset.theme,'dark');
});
test('blocked browser storage still allows theme changes',()=>{
  const b=browser({blocked:true});b.ready();b.choose('dark');assert.equal(b.root.dataset.theme,'dark');
});
test('cross-tab changes apply and removed preferences return to system',()=>{
  const b=browser();b.ready();b.storage('dark');assert.equal(b.root.dataset.theme,'dark');b.storage(null);assert.equal(b.root.dataset.theme,'light');assert.equal(b.buttons[2].attributes['aria-pressed'],'true');
});
test('invalid persisted preference safely follows system',()=>{
  const b=browser({stored:'invalid',dark:true});assert.equal(b.root.dataset.theme,'dark');assert.equal(b.root.dataset.themeMode,'system');
});
