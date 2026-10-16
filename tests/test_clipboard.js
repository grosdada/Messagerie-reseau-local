const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('macos/lan_messenger_mac.py','utf8');
const choose=source.slice(source.indexOf('function chooseDroppedImage('),source.indexOf("['dragenter'"));
const paste=source.slice(source.indexOf('function handlePaste('),source.indexOf("textBox.addEventListener('paste'"));
function run(items){
 const context={notice:{textContent:''},prevented:false};
 vm.createContext(context);
 vm.runInContext('let droppedFile=null;'+choose+paste+';globalThis.getFile=()=>droppedFile;',context);
 context.handlePaste({clipboardData:{items},preventDefault(){context.prevented=true}});
 return context;
}
const item=file=>({kind:'file',type:file.type,getAsFile:()=>file});
test('text paste remains native and does not replace attachment',()=>{
 const result=run([{kind:'string',type:'text/plain'}]);
 assert.equal(result.prevented,false);assert.equal(result.getFile(),null);
});
test('screenshot paste prepares original file without sending',()=>{
 const file={name:'capture.png',type:'image/png',size:512};
 const result=run([item(file)]);
 assert.equal(result.prevented,true);assert.equal(result.getFile(),file);
 assert.match(result.notice.textContent,/capture.png/);
});
test('oversized and unsupported pasted images are refused',()=>{
 for(const file of [{name:'big.png',type:'image/png',size:7*1024*1024},{name:'x.svg',type:'image/svg+xml',size:1}]){
  const result=run([item(file)]);assert.equal(result.getFile(),null);assert.ok(result.notice.textContent);
 }
});
