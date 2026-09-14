# Player identity HSV fallback

`serve_vote_v1` remains the authoritative first resolver and still requires five
usable serve/score votes. Its vote calculation, epoch boundaries, convention fill,
and successful records are unchanged.

Only epochs left in the artifact's `unresolved` list enter `hsv_fallback_v1`. The
fallback reads existing pose keypoints/bboxes and a bounded set of source-video
frames. For every segment it builds separate masked HSV histograms for the
shoulder-to-hip shirt and hip-to-knee shorts regions, averages multiple frames for
each court side, and scores both complete assignments: top=a/bottom=b and
top=b/bottom=a. Shirt/shorts weights are derived from how strongly each region
separates the two a/b prototypes, so an uninformative pair of dark shirts cannot
drown out clearly different shorts.

The winning assignment must clear both an absolute similarity floor and a margin
over the alternative. Missing or invalid crops, too few paired observations,
similar clothing, and ambiguous assignments abstain. A visual orientation change
requires two confident observations of the new orientation. Contiguous abstentions
that prefer that same orientation can move the boundary to the earliest supported
segment, but cannot confirm a change themselves. One contradictory observation is
therefore smoothed into the current run. A segment with no visual evidence remains
outside resolved epochs, and emitted epoch ranges never overlap. No pose model is
rerun. Score changes, serve order, winners, stroke alternation, and commentary never
enter the visual comparison or temporal confirmation.

When a serve-vote-resolved epoch has enough visual samples, its mapping anchors the
a/b colour prototypes and visual records use `resolved_by="hsv_fallback"`. If the
match has no usable authoritative anchor, the first sufficiently distinct visual
epoch initializes a=top and b=bottom deterministically and records
`resolved_by="hsv_default"`. Those are stable match-local labels across later side
swaps, but they do not prove scoreboard-row binding. The top-level
`visual_fallback.scoreboard_binding` flag and per-epoch similarity, margin, sample
count, trigger, and source retain that distinction; histograms are not persisted.

Each resolved visual run remains a normal `PlayerIdentityEpoch`, so consumers keep
using the inclusive `first_segment..last_segment` mapping API without HSV-specific
logic. Top-level diagnostics retain segment counts, abstentions, confirmed
transition support, selected boundaries, and resulting visual epoch count. Raw HSV
histograms are not stored.

The pose artifact is an optional direct dependency, so a match resolved by
`serve_vote_v1` retains its previous requirements and behavior. The fallback is
HSV-only and uses the project's existing OpenCV dependency. Unit tests require no
model or download.
