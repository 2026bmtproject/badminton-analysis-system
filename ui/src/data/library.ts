import type { CatalogEntry } from "../domain/models";
import type { ImportableMatch } from "./matchRepository";
import type { LocalAnalysisMatch } from "./pipelineTasks";

export type LibraryMatch = {
  id: string;
  name: string;
  kind: "match" | "fixture";
  local: LocalAnalysisMatch | null;
  review: CatalogEntry | null;
  candidate: ImportableMatch | null;
};

// The raw directory name is the local match ID; the published Review ID adds `match:`.
export function mergeLibrary(catalog: CatalogEntry[], local: LocalAnalysisMatch[], candidates: ImportableMatch[]): LibraryMatch[] {
  const rows = new Map<string, LibraryMatch>();
  for (const entry of catalog) {
    rows.set(entry.id, { id: entry.id, name: entry.name, kind: entry.kind, local: null, review: entry, candidate: null });
  }
  for (const match of local) {
    const id = `match:${match.id}`;
    const row = rows.get(id) ?? { id, name: match.id, kind: "match" as const, local: null, review: null, candidate: null };
    row.local = match;
    rows.set(id, row);
  }
  for (const candidate of candidates) {
    const id = `match:${candidate.id}`;
    const row = rows.get(id) ?? { id, name: candidate.id, kind: "match" as const, local: null, review: null, candidate: null };
    row.candidate = candidate;
    rows.set(id, row);
  }
  return [...rows.values()].sort((a, b) => a.kind === b.kind ? a.name.localeCompare(b.name, "zh-Hant") : a.kind === "match" ? -1 : 1);
}
