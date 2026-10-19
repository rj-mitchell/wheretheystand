/** Shared fetching for the committee pages: friendly messages while the data is still being collected. */
export async function getJSON<T>(url: string, missing: string): Promise<T> {
  const r = await fetch(url);
  if (r.status === 404) throw new Error(missing);
  if (r.status === 503) throw new Error("Committee information is being collected and will appear after the next update.");
  if (!r.ok) throw new Error("Could not load.");
  return (await r.json()) as T;
}

export const HOUSE_NAME: Record<string, string> = { Commons: "House of Commons", Lords: "House of Lords", Joint: "Joint committees" };
