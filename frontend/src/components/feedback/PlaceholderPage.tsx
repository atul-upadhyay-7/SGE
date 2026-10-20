import { Card, PageHeader } from "@/components/cards/Cards";
import { EmptyState } from "@/components/feedback/Feedback";

/**
 * Honest placeholder for a page whose backend feature is not available yet.
 *
 * The PRD forbids inventing data (PRD §3, §10), so instead of a mock screen a
 * not-yet-wired page states what it will show and what it is waiting on.
 */
export function PlaceholderPage({
  title,
  subtitle,
  waitingOn,
  planned,
}: {
  title: string;
  subtitle?: string;
  waitingOn: string;
  planned: string[];
}) {
  return (
    <div className="page">
      <PageHeader title={title} subtitle={subtitle} />
      <Card>
        <EmptyState
          title="Not available yet"
          detail={waitingOn}
          action={
            <ul className="placeholder__list">
              {planned.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          }
        />
      </Card>
    </div>
  );
}
