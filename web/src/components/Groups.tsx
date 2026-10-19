/** All-party and cross-party groups a member belongs to, for MP and peer pages. */

export interface MemberGroup {
  slug: string;
  parliament: string;
  parliament_name: string;
  title: string;
  role: string | null;
  is_officer: number;
  source: string;
  source_label: string;
  secretariat: string | null;
  benefits: number;
}

export function groupPath(g: { parliament: string; slug: string }): string {
  return `/groups/${g.parliament}/${g.slug}`;
}

export function GroupsSection({ groups, name }: { groups: MemberGroup[]; name: string }) {
  if (groups.length === 0) return null;
  const officer = groups.filter((g) => g.is_officer);
  const member = groups.filter((g) => !g.is_officer);
  const item = (g: MemberGroup) => (
    <li key={`${g.parliament}-${g.slug}`}>
      <a href={groupPath(g)}>{g.title}</a>
      {g.parliament !== "uk" && <span className="muted small"> ({g.parliament_name})</span>}
      {g.role && <span>: {g.role}</span>}
      {g.benefits > 0 && (
        <span className="muted small">
          {" "}
          · {g.benefits} registered benefit{g.benefits === 1 ? "" : "s"}
        </span>
      )}
    </li>
  );
  return (
    <section aria-labelledby="h-groups">
      <h2 id="h-groups">All-party and cross-party groups</h2>
      <p>
        {[
          officer.length > 0 && `an officer of ${officer.length} group${officer.length === 1 ? "" : "s"}`,
          member.length > 0 && `a listed member of ${member.length}${officer.length > 0 ? " more" : ` group${member.length === 1 ? "" : "s"}`}`,
        ]
          .filter(Boolean)
          .join(" and ")
          .replace(/^/, `${name} is `)}
        .
      </p>
      <p className="muted small">
        Officers are from the official register; other members are from lists the groups supplied, so membership is
        incomplete. Groups are informal and some are funded by outside organisations; belonging to one is not an
        endorsement of its funders. See <a href="/groups">all groups</a>.
      </p>
      {officer.length > 0 && (
        <details className="interest-category">
          <summary>Officer ({officer.length})</summary>
          <ul className="interests">{officer.map(item)}</ul>
        </details>
      )}
      {member.length > 0 && (
        <details className="interest-category">
          <summary>Member ({member.length})</summary>
          <ul className="interests">{member.map(item)}</ul>
        </details>
      )}
    </section>
  );
}
