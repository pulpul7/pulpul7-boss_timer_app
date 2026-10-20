/* Actual canvas renderer and computed-style bridge, without a browser/network. */
const checks=[];
function check(ok,message){if(!ok)throw Error(message);checks.push(message);}
const theme=source.split('// BEGIN WEB COPY IMAGE THEME')[1].split('// END WEB COPY IMAGE THEME')[0];
const render=source.slice(source.indexOf('function fit(ctx,'),source.indexOf('function releaseImage('));
function fixture(light=true,selected='spring') {
  const values=light?{'--bg':'#fff0f6','--panel':'#fff0f5','--row':'#ffe7f1','--text':'#5c163b','--muted':'#854060',
    '--blue':'#b91562','--line':'#dd93b5','--invasion':'#f8cee8'}:
    {'--bg':'#151b24','--panel':'#253346','--row':'#253346','--text':'#edf1f7','--muted':'#b1bdce','--blue':'#91c7ff','--line':'#41516a'};
  const images=[],fills=[],texts=[],gradients=[];
  const ctx={fillStyle:'',strokeStyle:'',globalAlpha:1,font:'',
    fillRect(...bounds){fills.push({fill:this.fillStyle,bounds});},strokeRect(){},
    fillText(value,...at){texts.push({value,color:this.fillStyle,font:this.font,at});},
    measureText:value=>({width:String(value).length*8}),beginPath(){},moveTo(){},lineTo(){},stroke(){},save(){},restore(){},
    createLinearGradient(...bounds){const gradient={bounds,stops:[],addColorStop(offset,color){this.stops.push({offset,color});}};gradients.push(gradient);return gradient;},
    drawImage(...args){images.push(args);}};
  const scene={complete:true,naturalWidth:1200,naturalHeight:800,src:'blob:https://example.test/photo'};
  const dialog={},elements={'copy-dialog':dialog,'spring-scene':scene,'winter-scene':scene,'rain-scene':scene,'avengers-scene':scene};
  const canvas={width:0,height:0,getContext:()=>ctx,toBlob:(callback,type)=>callback({type})};
  const document={documentElement:{hasAttribute:()=>light},createElement:()=>canvas};
  const getComputedStyle=node=>node===dialog?{getPropertyValue:key=>values[key]||''}:node.css;
  const api=new Function('document','element','getComputedStyle','WebFonts','selectedTheme','location','URL',`
    const colorOf=()=> '#ffffff',backgroundOf=()=> '#87ceeb',dayKey=()=> 'day',clock=()=> '12:00:00',
      dateLabel=()=> 'date',displayName=row=>row.name,restDuration=()=> '5시간 32분',types={boss:'boss',break:'rest'};
    ${theme}\n${render}
    return {copyImagePalette,copyImageRow,copyImageScene,copyImageGradient,drawImage};`)(document,id=>elements[id],getComputedStyle,
      {family:()=> 'CuteFont'},selected,{href:'https://example.test/',origin:'https://example.test'},URL);
  return {api,scene,canvas,ctx,values,images,fills,texts,gradients};
}
const f=fixture(),palette=f.api.copyImagePalette();
check(palette.bg==='#fff0f6'&&palette.text==='#5c163b'&&palette.font==='CuteFont','image palette comes from current theme and font');
const children={'.boss-name':{css:{color:'rgb(80, 10, 30)'}},time:{css:{color:'rgb(70, 10, 20)'}},'.event-state':{css:{color:'rgb(90, 40, 60)'}}};
const item={css:{backgroundColor:'rgba(0, 0, 0, 0)',borderTopColor:'rgb(220, 140, 170)',
  backgroundImage:'linear-gradient(100deg, rgb(255, 245, 248) 0%, rgba(255, 225, 238, 0.9) 60%, rgba(249, 212, 237, 0.6) 100%)'},
  querySelector:selector=>children[selector]};
const row=f.api.copyImageRow({name:'boss',kind:'boss',at:2000,important:true},item,palette);
check(row.shareColor==='rgb(80, 10, 30)'&&row.shareTimeColor==='rgb(70, 10, 20)','name and clock ink match themed preview rather than dark defaults');
const gradient=f.api.copyImageGradient(f.ctx,item.css.backgroundImage,24,200,852,58);
check(gradient.stops.map(s=>s.offset).join(',')==='0,0.6,1','gradient alpha colors and explicit stop positions survive canvas export');
const scene=f.api.copyImageScene();
check(scene===f.scene,'loaded current photographic theme is reused without a new request');
const info={palette,scene,now:1000,context:'server',range:'range',stamp:'stamp',stale:false};
const blob=await f.api.drawImage([row],info,0,1);
check(blob.type==='image/png'&&f.images.length===1,'PNG export includes the current theme photograph');
check(f.texts.find(t=>t.value==='server').color===palette.text,'image heading uses the current theme foreground');
check(f.texts.some(t=>t.color===row.shareColor)&&f.texts.some(t=>t.color===row.shareTimeColor),'rendered boss and clock use captured preview colors');
check(!f.fills.some(f=>f.fill==='#151b24'),'light export has no hardcoded dark canvas fill');
check(f.texts.every(t=>t.font.includes('CuteFont')),'all exported text uses the selected font');
f.scene.src='https://foreign.test/photo';check(f.api.copyImageScene()===null,'foreign-origin optional images are omitted to keep PNG readable');
f.scene.src='blob:https://foreign.test/photo';check(f.api.copyImageScene()===null,'foreign blob URLs are excluded too');
f.scene.complete=false;check(f.api.copyImageScene()===null,'unloaded scenes do not block themed image export');
const dark=fixture(false,'default'),darkPalette=dark.api.copyImagePalette();
const avengers=fixture(false,'avengers');avengers.scene.src='data:image/jpeg;base64,AQ==';
check(avengers.api.copyImageScene()===avengers.scene,'Avengers export can reuse its embedded scene without another request');
await dark.api.drawImage([{name:'boss',kind:'boss',at:2000}],{...info,palette:darkPalette,scene:null},0,1);
check(dark.fills.some(f=>f.fill==='#151b24'),'default dark theme still exports its own background');
const rest=f.api.copyImageRow({name:'휴식',kind:'break',at:0,end:5000},item,palette);
check(rest.shareFill.image===item.css.backgroundImage&&rest.shareStateColor==='rgb(90, 40, 60)','rest cards retain the same themed fill and readable duration ink');
return checks;
