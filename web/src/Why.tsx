import type { Classification, PlayPattern } from "./api";

// Why a player has their class. The history score joins two things: evidence events (single moments,
// listed on the page) and the overall play pattern (every duel of every match, scored against clean
// players). The pattern produces no events, so without this a player can be Elevated with an empty list.

export function Why({
  classification,
  eventScore,
  pattern,
  eventCount,
  elevatedThreshold = 0.25,
}: {
  classification: Classification | null | undefined;
  eventScore: number | null | undefined;
  pattern: PlayPattern | null | undefined;
  eventCount: number | null;
  /** History cut-off for Elevated (history.classification.thresholds), sent by the API. */
  elevatedThreshold?: number;
}) {
  if (!classification || !["ELEVATED", "HIGH", "VERY_HIGH"].includes(classification)) return null;
  const fromPattern = (pattern?.strength ?? 0) > 0;
  const fromEvents = (eventScore ?? 0) > 0 && (eventCount ?? 1) > 0;
  return (
    <section className="panel why">
      <h2>Why this class</h2>
      {fromPattern && (
        <>
          <p>
            <strong>Overall play pattern.</strong> Across {pattern?.matches ?? "their"} analyzed matches, this player's
            typical aim and timing is more unusual than{" "}
            {pattern?.percentile != null ? `${(100 * pattern.percentile).toFixed(1)}%` : "most"} of clean players. It is
            measured over every duel rather than single moments, so it does not show up as evidence events.
            {!fromEvents && " On its own it can reach Elevated, never High."} The numbers are under Play pattern below.
          </p>
          {pattern?.suddenChange && (
            <p className="small">Their latest match is clearly more unusual than their earlier ones.</p>
          )}
        </>
      )}
      {fromEvents ? (
        <p>
          <strong>Evidence events.</strong> Specific moments, listed under Strongest evidence events,
          {(eventScore ?? 0) >= elevatedThreshold ? " are enough on their own for this class." : " add to it."}
        </p>
      ) : (
        <p className="muted small">No single moment was unusual enough to become an evidence event.</p>
      )}
      {!fromPattern && !fromEvents && (
        <p className="muted small">A breakdown of this class isn't available yet.</p>
      )}
    </section>
  );
}
