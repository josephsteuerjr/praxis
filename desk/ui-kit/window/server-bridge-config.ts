import type { Config } from './views/settings-frame';

export type ServerBridge={id:string;name:string;external?:string;phase?:string;ports:Record<string,number>};

/** One saved connection supplies both routes, while preserving unrelated settings. */
export function useBridge(config:Config,bridge:ServerBridge) {
  if(bridge.external)config.phone={...config.phone,enabled:true,mode:'stable',external:bridge.external,bridge_profile:bridge.id};
  if(bridge.ports.local_proxy_port)config.network={...config.network,bridge_profile:bridge.id,
    proxy:{mode:'proxy',protocol:'http',host:'127.0.0.1',port:String(bridge.ports.local_proxy_port),username:'',password:''}};
}
