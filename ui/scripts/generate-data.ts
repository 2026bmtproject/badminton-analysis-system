import { readFile, mkdir, writeFile } from "node:fs/promises";
import { adapt, validateFixture, manifestSchema, type Input } from "./adapter";
const manifest = manifestSchema.parse(
  JSON.parse(await readFile("fixtures/manifest.json", "utf8")),
);
await mkdir("public/generated", { recursive: true });
for (const scenario of manifest.scenarios) {
  const raw: Input = {};
  for (const name of scenario.stages) {
    // Declared but unreadable is an error input, never an omitted optional stage.
    try {
      raw[name] = JSON.parse(
        await readFile(`fixtures/stages/${name}.json`, "utf8"),
      );
    } catch {
      raw[name] = { readError: true };
    }
  }
  const model = validateFixture(adapt(raw, manifest, scenario.name));
  await writeFile(
    `public/generated/${scenario.id}.json`,
    JSON.stringify(model, null, 2) + "\n",
  );
}
await writeFile(
  "public/generated/catalog.json",
  JSON.stringify(manifest.scenarios.map((s) => ({ id: s.id, name: s.name }))) + "\n",
);
console.log("ReviewModel generated from stage envelopes.");
