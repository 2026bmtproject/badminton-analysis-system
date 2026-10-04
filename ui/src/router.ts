import {
  createRouter,
  createWebHistory,
  type RouterHistory,
  type RouteRecordRaw,
} from "vue-router";

export const routes: RouteRecordRaw[] = [
  {
    path: "/",
    redirect: { name: "matches" },
  },
  {
    path: "/matches",
    name: "matches",
    component: () => import("./pages/MatchesPage.vue"),
  },
  {
    path: "/matches/:matchId",
    component: () => import("./layouts/MatchShell.vue"),
    redirect: (to) => ({
      name: "match-review",
      params: { matchId: to.params.matchId },
    }),
    children: [
      {
        path: "review",
        name: "match-review",
        component: () => import("./pages/ReviewPage.vue"),
      },
      {
        path: "rallies",
        name: "match-rallies",
        component: () => import("./pages/RalliesPage.vue"),
      },
      {
        path: "rallies/:segmentId",
        name: "rally-detail",
        redirect: (to) => ({
          name: "match-review",
          params: { matchId: to.params.matchId },
          query: { segment: to.params.segmentId, stroke: to.query.stroke },
        }),
      },
    ],
  },
];

export function createAppRouter(history?: RouterHistory) {
  return createRouter({
    history: history ?? createWebHistory(),
    routes,
    scrollBehavior: (_to, _from, savedPosition) => savedPosition ?? { top: 0 },
  });
}
