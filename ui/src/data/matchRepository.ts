import { z } from "zod";
import { type CatalogEntry, type MatchModel } from "../domain/models";
import { parseMatchModel } from "./matchParser";

const catalogRow = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  url: z.string().min(1),
});

async function fetchJson(url: string, fresh = false): Promise<unknown> {
  const response = await fetch(url, fresh ? { cache: "no-store" } : undefined);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const body: unknown = await response.json();
  return body;
}

export async function loadCatalog(): Promise<CatalogEntry[]> {
  const [fixtures, matches] = await Promise.all([
    fetchJson("/generated/catalog.json"),
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

export async function loadMatch(
  entry: CatalogEntry,
  options: { fresh?: boolean } = {},
): Promise<MatchModel> {
  return parseMatchModel(await fetchJson(entry.url, options.fresh));
}
