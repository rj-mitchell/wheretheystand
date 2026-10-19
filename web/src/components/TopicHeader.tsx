import { fmtDate } from "../render";
import type { TopicInfo } from "../types";

/** Above a topic briefing: what the topic is, how it is kept up to date, and its earlier versions. */
export function TopicHeader({
  topic,
  preparedAt,
  recordsUpTo,
}: {
  topic: TopicInfo;
  preparedAt?: string;
  recordsUpTo?: string | null;
}) {
  const earlier = (topic.versions ?? []).slice(1);
  const every = topic.refresh_days === 7 ? "once a week" : `once every ${topic.refresh_days} days`;
  return (
    <header className="topic-header">
      <p className="eyebrow">
        <a href="/topics">Topic briefings</a> · {topic.area}
      </p>
      <h1 className="topic-title">{topic.title}</h1>
      {topic.description && <p className="topic-description">{topic.description}</p>}
      <p className="topic-note">
        Kept up to date: rewritten when Parliament has said something new on the subject, at most {every}.
        {preparedAt && (
          <>
            {" "}
            This version was prepared {fmtDate(preparedAt.slice(0, 10))}
            {recordsUpTo ? `, from records up to ${fmtDate(recordsUpTo)}` : ""}.
          </>
        )}
      </p>
      <p className="small feed-link">
        <a href={`/feeds/topics/${topic.slug}.xml`}>Follow this topic: each new version, as a feed (RSS)</a>
      </p>
      {earlier.length > 0 && (
        <details className="topic-versions">
          <summary>Earlier versions ({earlier.length})</summary>
          <ul>
            {earlier.map((v) => (
              <li key={v.briefing_id}>
                <a href={`/b/${v.briefing_id}`}>Prepared {fmtDate(v.prepared_at.slice(0, 10))}</a>
              </li>
            ))}
          </ul>
        </details>
      )}
    </header>
  );
}
