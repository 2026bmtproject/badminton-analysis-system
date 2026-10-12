import { test } from "node:test";
import assert from "node:assert/strict";
import { createMemoryHistory } from "vue-router";
import { createAppRouter, routes } from "../src/router";

test("root redirects to the repository-backed Matches route", () => {
  const router = createAppRouter(createMemoryHistory());
  const root = routes.find((route) => route.path === "/");
  assert.deepEqual(root?.redirect, { name: "matches" });
  assert.equal(router.resolve({ name: "matches" }).fullPath, "/matches");
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

test("the Rallies list and its detail links are gone", () => {
  const matchRoute = routes.find((route) => route.path === "/matches/:matchId");
  assert.deepEqual(matchRoute?.children?.map((route) => route.name), ["match-review"]);
  const router = createAppRouter(createMemoryHistory());
  assert.equal(router.resolve("/matches/match:real/rallies").matched.length, 0);
});

test("routes cover analysis, tasks and settings and none of the retired pages", () => {
  const routePaths = JSON.stringify(routes);
  assert.match(routePaths, /analysis|tasks|settings/i);
  assert.doesNotMatch(routePaths, /watch|highlights|system/i);
});
