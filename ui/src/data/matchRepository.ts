import { z } from "zod";
import { type CatalogEntry, type MatchModel } from "../domain/models";
import { parseMatchModel } from "./matchParser";

const catalogRow = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  url: z.string().min(1),
});
const importableRow = z.object({
  id: z.string(),
  available: z.boolean(),
  reason: z.string().optional(),
});
export type ImportableMatch = z.infer<typeof importableRow>;

async function fetchJson(url: string, fresh = false): Promise<unknown> {
  const response = await fetch(url, fresh ? { cache: "no-store" } : undefined);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body: unknown = await response.json();
  return body;
}

export async function loadCatalog(): Promise<CatalogEntry[]> {
  const [fixtures, matches] = await Promise.all([
    fetchJson("/generated/catalog.json").catch(() => []),
    fetchJson("/matches/catalog.json").catch(() => []),
  ]);
  const fixtureRows = z.array(catalogRow.omit({ url: true })).parse(fixtures);
  const matchRows = z.array(catalogRow).parse(matches);
  const visibleFixtures = import.meta.env.DEV ? fixtureRows : [];
  return [
    ...matchRows.map((entry) => ({ ...entry, kind: "match" as const })),
    ...visibleFixtures.map((entry) => ({
      ...entry,
      url: `/generated/${entry.id}.json`,
      kind: "fixture" as const,
    })),
  ];
}

export async function listLocalMatches(): Promise<ImportableMatch[]> {
  const body = await fetchJson("/api/local-matches", true);
  return z.object({ matches: z.array(importableRow) }).parse(body).matches;
}

export async function importLocalMatch(id: string): Promise<string> {
  const response = await fetch("/api/local-matches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const error = z.object({ error: z.string() }).safeParse(body);
    throw new Error(error.success ? error.data.error : `HTTP ${response.status}`);
  }
  return z.object({ id: z.string() }).parse(body).id;
}

export async function loadMatch(
  entry: CatalogEntry,
  options: { fresh?: boolean } = {},
): Promise<MatchModel> {
  return parseMatchModel(await fetchJson(entry.url, options.fresh));
}
