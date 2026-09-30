import { fileURLToPath } from "node:url";
import { importMatch } from "./local-matches";
const id = process.argv[2];
if (!id) throw new Error("Usage: npm run import:match -- <match-folder-name>");
const model = await importMatch(fileURLToPath(new URL("..", import.meta.url)), id);
const hits = model.rallies.flatMap((r) => r.hits ?? []);
console.log(JSON.stringify({ match: id, segments: model.rallies.length, hits: hits.length,
  withScores: model.rallies.filter((r) => r.score !== null).length,
  unknownStrokes: hits.filter((h) => h.type === "未知球種" || h.type === null).length,
  states: model.states }, null, 2));
