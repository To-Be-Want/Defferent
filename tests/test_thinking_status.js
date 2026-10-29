// Test the real status renderer and message handlers without external browser packages.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
class Element {
  constructor() { this.children=[]; this.attributes={}; this.dataset={}; this.style={}; }
  setAttribute(k,v) { this.attributes[k]=v; }
  append(...nodes) { this.children.push(...nodes); }
  before(node) { elements.set(node.id,node); }
  querySelector(selector) { return this.children.find(n=>n.className===selector.slice(1)); }
}
elements.set('chat-messages',new Element());
let now=0, nextTimer=1;
const timers=new Map(), replies=[];
const context={
  document:{getElementById:id=>elements.get(id),createElement:()=>new Element()},
  localStorage:{getItem:()=>null,setItem(){}},
  window:{appendChatMessage:t=>replies.push(t),expandChatPanel(){}},
  console:{log(){},warn(){},error(){}},
  Date:{now:()=>now},setInterval:fn=>{const id=nextTimer++;timers.set(id,fn);return id;},
  clearInterval:id=>timers.delete(id),
};
const source=fs.readFileSync('cosight_server/web/js/message.js','utf8');
vm.runInNewContext(source.slice(0,source.indexOf('window.messageService = new'))+'\nthis.Type=MessageService;',context);
const service=Object.create(context.Type.prototype);
function receive(data) { service.receiveMessage({topic:'test',data}); }
receive({type:'multi-modal',from:'human'});
const status=elements.get('study-chat-status');
assert.equal(status.hidden,false);assert.equal(status.attributes.role,'status');assert.equal(timers.size,1);
receive({type:'study-agent-progress',stage:'locating'});
assert(status.querySelector('.study-thinking-label').textContent.includes('定位对应知识点'));
receive({type:'study-agent-progress',stage:'reading',count:2});
assert(status.querySelector('.study-thinking-label').textContent.includes('2 段'));
receive({type:'study-agent-progress',stage:'thinking',reasoning:'PRIVATE_TRACE_DO_NOT_SHOW'});
assert(!status.querySelector('.study-thinking-label').textContent.includes('PRIVATE_TRACE'));
now=4000;[...timers.values()].forEach(fn=>fn());
assert.equal(status.querySelector('.study-thinking-elapsed').textContent,'4 秒');
receive({type:'multi-modal',from:'ai',initData:[{type:'text',value:'按课件讲解。'}]});
assert.equal(replies[0],'按课件讲解。');assert.equal(status.hidden,true);assert.equal(timers.size,0);
service.setChatStatus('连接断开，消息未发送',false);
assert.equal(status.dataset.pending,'false');assert.equal(timers.size,0);
service.setChatStatus('继续处理…');receive({type:'control-status-message'});
assert.equal(timers.size,0);assert.equal(status.hidden,true);
console.log('Thinking status: stages, elapsed time, reply cleanup, disconnect and private-trace exclusion passed.');
