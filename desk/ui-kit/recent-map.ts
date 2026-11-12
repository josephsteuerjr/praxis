/**
 * A Map that keeps only the most recently used entries.
 *
 * 12.11: the window cached every run's full detail (steps and tool outputs) for the
 * whole life of the page. A day of autonomous work grew one WebView2 renderer to
 * 3.8 GB. Details are refetched on demand, so a bounded cache loses nothing.
 */
export class RecentMap<K, V> extends Map<K, V> {
  readonly limit: number;

  constructor(limit: number) {
    super();
    this.limit = limit;
  }

  get(key: K): V | undefined {
    if (!super.has(key)) return undefined;
    const value = super.get(key) as V;
    super.delete(key);
    super.set(key, value);
    return value;
  }

  set(key: K, value: V): this {
    super.delete(key);
    super.set(key, value);
    while (this.size > this.limit) super.delete(this.keys().next().value as K);
    return this;
  }
}
