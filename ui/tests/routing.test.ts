import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createMemoryHistory } from "vue-router";
import { createAppRouter, routes } from "../src/router";

test("root redirects to the repository-backed Matches route", () => {
  const router = createAppRouter(createMemoryHistory());
  const root = routes.find((route) => route.path === "/");
  assert.deepEqual(root?.redirect, { name: "matches" });
  assert.equal(router.resolve({ name: "matches" }).fullPath, "/matches");

  const matches = readFileSync("src/pages/MatchesPage.vue", "utf8");
  assert.match(matches, /await loadCatalog\(\)/);
  assert.match(matches, /v-for="entry in entries"/);
  assert.match(matches, /params: \{ matchId: entry\.id \}/);
  assert.match(matches, /class="match-row"/);
});

test("valid Match selection resolves the exact catalog identity", () => {
  const router = createAppRouter(createMemoryHistory());
  const resolved = router.resolve({
    name: "match-review",
    params: { matchId: "yt_u7yDYU4b7CU" },
  });
  assert.equal(resolved.fullPath, "/matches/yt_u7yDYU4b7CU/review");
  assert.equal(resolved.params.matchId, "yt_u7yDYU4b7CU");
});

test("Rallies remains a MatchShell child and old detail links redirect into Review", () => {
  const router = createAppRouter(createMemoryHistory());
  assert.equal(
    router.resolve({ name: "match-rallies", params: { matchId: "match:real" } })
      .fullPath,
    "/matches/match:real/rallies",
  );
  assert.equal(
    router.resolve({
      name: "rally-detail",
      params: { matchId: "match:real", segmentId: 42 },
      query: { stroke: "671" },
    }).fullPath,
    "/matches/match:real/rallies/42?stroke=671",
  );
  const matchRoute = routes.find((route) => route.path === "/matches/:matchId");
  const detailRoute = matchRoute?.children?.find((route) => route.name === "rally-detail");
  assert.equal(typeof detailRoute?.redirect, "function");
  const redirect = detailRoute!.redirect as (to: unknown) => unknown;
  assert.deepEqual(redirect({ params: { matchId: "match:real", segmentId: "42" }, query: { stroke: "671" } }), {
    name: "match-review",
    params: { matchId: "match:real" },
    query: { segment: "42", stroke: "671" },
  });
});

test("MatchShell loads one exact MatchModel and fails closed for an invalid id", () => {
  const shell = readFileSync("src/layouts/MatchShell.vue", "utf8");
  assert.equal((shell.match(/await loadMatch\(selected\)/g) ?? []).length, 1);
  assert.match(shell, /item\.id === matchId/);
  assert.match(shell, /找不到比賽/);
  assert.doesNotMatch(shell, /catalog\.value\[0\]/);
  assert.match(shell, /context\.model\.value = null/);
  assert.match(shell, /context\.model\.value = model/);
});

test("one MatchShell workspace owns the routed Review", () => {
  const shell = readFileSync("src/layouts/MatchShell.vue", "utf8");
  const context = readFileSync("src/state/matchContext.ts", "utf8");
  const review = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const app = readFileSync("src/App.vue", "utf8");

  assert.equal(
    (context.match(/useReviewWorkspace\(model, player\)/g) ?? []).length,
    1,
  );
  assert.match(shell, /createMatchContext\(\)/);
  assert.match(shell, /provideMatchContext\(context\)/);
  assert.match(shell, /<RouterView v-else-if="context\.model\.value" \/>/);
  assert.match(review, /const context = useMatchContext\(\)/);
  assert.doesNotMatch(review, /useReviewWorkspace/);
  assert.match(app, /<RouterView \/>/);
});

test("Match switching retains the verified workspace reset contract", () => {
  const shell = readFileSync("src/layouts/MatchShell.vue", "utf8");
  const workspace = readFileSync("src/state/useReviewWorkspace.ts", "utf8");
  assert.match(shell, /watch\(\(\) => route\.params\.matchId/);
  assert.match(workspace, /currentTimeSec\.value = 0/);
  assert.match(workspace, /clearSelection\(\)/);
  assert.match(workspace, /player\.value\?\.seek\(0\)/);
});

test("browser-style back semantics use real links and no duplicate Match Library", () => {
  const shell = readFileSync("src/layouts/MatchShell.vue", "utf8");
  const review = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const routePaths = JSON.stringify(routes);
  assert.match(
    shell,
    /<RouterLink class="match-back-link" :to="\{ name: 'matches' \}">/,
  );
  assert.match(shell, /aria-label="比賽導覽"/);
  assert.match(shell, />\s*Review\s*<\/RouterLink>/);
  assert.match(shell, />\s*Rallies\s*<\/RouterLink>/);
  assert.match(shell, /route\.name === 'match-rallies'/);
  assert.match(review, /aria-label="工作區版面"/);
  assert.doesNotMatch(routePaths, /analysis|watch|highlights|system/i);
  assert.doesNotMatch(
    `${shell}\n${review}`,
    /libraryOpen|MatchLibrary|切換比賽/,
  );
});
