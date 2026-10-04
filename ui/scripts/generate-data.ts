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
  JSON.stringify([
    ...manifest.scenarios.map((s) => ({ id: s.id, name: s.name })),
    { id: "long-layout", name: "一小時布局測試" },
  ]) + "\n",
);
console.log("ReviewModel generated from stage envelopes.");

// Layout-only synthetic source: never bound to the 44-second demo video.
const segments = Array.from({ length: 120 }, (_, i) => {
  const start = i * 30 + 2,
    end = start + (i % 5 === 0 ? 1 : 12 + (i % 12));
  return {
    start_frame: start * 25,
    end_frame: end * 25,
    start_sec: start,
    end_sec: end,
    duration_sec: end - start,
  };
});
const longModel = validateFixture(
  adapt(
    { segments: { fps: 25, segments } },
    {
      ...manifest,
      title: "一小時布局測試",
      video: "",
      duration: 3600,
      identities: {},
    },
    "120 個合成片段",
  ),
);
longModel.layoutOnly = true;
await writeFile(
  "public/generated/long-layout.json",
  JSON.stringify(longModel, null, 2) + "\n",
);

// Reproducible v3 interaction fixtures, using the same short test video.
// These synthetic events validate controls only, not video analysis accuracy.
const interactionSegments = [
  {
    start_frame: 0,
    end_frame: 500,
    start_sec: 0,
    end_sec: 20,
    duration_sec: 20,
  },
  {
    start_frame: 525,
    end_frame: 1075,
    start_sec: 21,
    end_sec: 43,
    duration_sec: 22,
  },
];
const interactionRaw: Input = {
  segments: { fps: 25, segments: interactionSegments },
  events: {
    events: [
      { frame: 50 },
      { frame: 63 },
      ...Array.from({ length: 60 }, (_, i) => ({ frame: 530 + i * 9 })),
    ],
  },
};
const cases = [
  { id: "dense-test", name: "合成密集擊球驗證", raw: interactionRaw },
  {
    id: "error-test",
    name: "擊球讀取失敗驗證",
    raw: { ...interactionRaw, events: { readError: true } },
  },
  {
    id: "zero-test",
    name: "零次擊球驗證",
    raw: { ...interactionRaw, events: { events: [] } },
  },
];
for (const sample of cases) {
  const result = adapt(
    sample.raw,
    { ...manifest, title: sample.name, identities: {} },
    sample.id,
  );
  if (sample.id !== "error-test") validateFixture(result);
  await writeFile(
    `public/generated/${sample.id}.json`,
    JSON.stringify(result, null, 2) + "\n",
  );
}
const catalogPath = "public/generated/catalog.json";
const catalog = JSON.parse(await readFile(catalogPath, "utf8"));
await writeFile(
  catalogPath,
  JSON.stringify([...catalog, ...cases.map(({ id, name }) => ({ id, name }))]) +
    "\n",
);
