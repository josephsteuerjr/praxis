/** Only arrivals observed during this window session; historical/repainted rows stay silent. */
export class VoiceArrivals {
  private rooms=new Map<string,Set<string>>();
  private since=new Date().toISOString();
  observe(room:string, rows:Array<{key:string;at:string}>, enabled:boolean):Set<string> {
    const previous=this.rooms.get(room),fresh=new Set<string>();
    const seen=previous || new Set<string>();
    for(const row of rows){if(previous && !seen.has(row.key) && row.at>=this.since && enabled)fresh.add(row.key);seen.add(row.key);}
    // Bound retained IDs without turning any historical row into an arrival.
    if(seen.size>2000){const retained=new Set([...seen].slice(-1000));this.rooms.set(room,retained);}else this.rooms.set(room,seen);
    return fresh;
  }
}
