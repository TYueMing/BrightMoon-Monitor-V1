// Snapshot stream remains backwards compatible; v2 business commands are separate.
export async function request(path, options={}) {
  const response=await fetch(path,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(6000),...options});
  if(response.status===304)return {unchanged:true,producer_age_ms:Number(response.headers.get('X-Producer-Age')),server_time_ms:Number(response.headers.get('X-Server-Time')),presence:{viewer_id:response.headers.get('X-Viewer-Id'),challenge:response.headers.get('X-Viewer-Challenge')}};
  const value=await response.json();
  if(!response.ok){const error=new Error(value.error||'暂时无法连接电脑');error.status=response.status;throw error;}
  return value;
}

export class Transport {
  constructor(onSnapshot,onHeartbeat,onOffline,onExpired){Object.assign(this,{onSnapshot,onHeartbeat,onOffline,onExpired});this.version=0;this.timer=null;this.stream=null;this.failures=0;this.running=false;this.viewer=null;this.lastAck=-Infinity;}
  stop(){this.running=false;this.version++;clearTimeout(this.timer);this.stream?.close();this.stream=null;}
  async confirm(value,version){
    const proof=value?.presence;
    if(!proof?.challenge||version!==this.version||!this.running||document.hidden||performance.now()-this.lastAck<4500)return;
    this.lastAck=performance.now();
    try{await request('/api/v1/presence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(proof)});}catch{this.lastAck=-Infinity;}
  }
  async start(){this.stop();this.failures=0;this.running=true;const version=this.version;await this.connect(version);}
  async connect(version){
    if(!this.running||version!==this.version||document.hidden)return;
    try{
      await request('/api/v1/session');
      if(!this.running||version!==this.version)return;
      const registration=await request('/api/v1/viewers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({viewer_id:this.viewer})});
      if(!this.running||version!==this.version)return;
      this.viewer=registration.viewer_id;
      const suffix=this.viewer?'?viewer_id='+encodeURIComponent(this.viewer):'';
      const snapshot=await request('/api/v1/snapshot'+suffix);
      if(!this.running||version!==this.version)return;
      this.onSnapshot(snapshot);this.confirm(snapshot,version);
      if(this.failures>=3){this.timer=setTimeout(()=>this.connect(version),2000);return;}
      const stream=new EventSource('/api/v1/events'+suffix);this.stream=stream;
      stream.addEventListener('service-changed',()=>{if(version===this.version)window.dispatchEvent(new Event('solo-service-changed'));});
      stream.addEventListener('device-saved',()=>{
        if(version!==this.version)return;
        request('/api/v1/session').catch(()=>this.retry(version));
      });
      stream.addEventListener('snapshot',event=>{if(version!==this.version)return;try{const value=JSON.parse(event.data);this.onSnapshot(value);this.confirm(value,version);this.failures=0;}catch{this.retry(version);}});
      stream.addEventListener('heartbeat',event=>{if(version===this.version){try{const value=JSON.parse(event.data);this.onHeartbeat(value);this.confirm(value,version);}catch{this.retry(version);}}});
      stream.addEventListener('session-ended',()=>{this.stop();this.onExpired();});
      stream.onerror=()=>this.retry(version);
    }catch(error){
      if(version!==this.version||!this.running)return;
      if(error.status===401){this.stop();this.onExpired();}else{this.retry(version);}
    }
  }
  retry(version){if(version!==this.version||!this.running)return;this.stream?.close();this.stream=null;clearTimeout(this.timer);this.failures++;this.onOffline();this.timer=setTimeout(()=>this.connect(version),Math.min(15000,1000*2**Math.min(this.failures-1,4))+Math.random()*250);}
}
