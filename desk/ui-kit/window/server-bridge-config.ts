import type { Config } from './views/settings-frame';

export type ServerBridge={id:string;name:string;external?:string;phase?:string;ports:Record<string,number>};
/** Which parts of the agent go through the bridge; nothing is implied. */
export type BridgeTargets={phone?:boolean;network?:boolean;relay?:boolean};

/** Bind the chosen routes to one saved bridge, preserving unrelated settings. */
export function useBridge(config:Config,bridge:ServerBridge,targets:BridgeTargets) {
  if(targets.phone&&bridge.external)config.phone={...config.phone,enabled:true,mode:'stable',external:bridge.external,bridge_profile:bridge.id};
  const port=bridge.ports.local_proxy_port;
  if(!port)return;
  const proxy=()=>({mode:'proxy',protocol:'http',host:'127.0.0.1',port:String(port),username:'',password:''});
  if(targets.network)config.network={...config.network,bridge_profile:bridge.id,proxy:proxy()};
  if(targets.relay)config.relay={...config.relay,bridge_profile:bridge.id,proxy:proxy()};
}
