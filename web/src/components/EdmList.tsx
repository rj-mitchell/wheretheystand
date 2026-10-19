import { fmtDate } from "../render";

export interface EdmMember {
  name: string | null;
  party: string | null;
  number: number | null;
  slug: string | null;
}
export interface EdmRow {
  id: number;
  uin: string | null;
  title: string;
  date_tabled: string | null;
  status: string | null;
  sponsors_count: number | null;
  prayer: number;
  sponsor: EdmMember;
}
export function EdmList({ items }: { items: EdmRow[] }) {
  return (
    <ol className="edm-list">
      {items.map((e) => (
        <li key={e.id}>
          <a href={`/edms/${e.id}`}>{e.title}</a>
          <span className="muted small">
            {" "}
            {e.uin ? `EDM ${e.uin}, ` : ""}
            {e.date_tabled ? fmtDate(e.date_tabled) : ""}
            {e.sponsor.name ? ` · ${e.sponsor.name}${e.sponsor.party ? ` (${e.sponsor.party})` : ""}` : ""}
            {e.sponsors_count != null ? ` · ${e.sponsors_count} signature${e.sponsors_count === 1 ? "" : "s"}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}
