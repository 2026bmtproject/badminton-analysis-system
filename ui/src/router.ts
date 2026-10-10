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
  { path: "/tasks", name: "tasks", component: () => import("./pages/TasksPage.vue") },
  { path: "/settings", name: "settings", component: () => import("./pages/SettingsPage.vue") },
  { path: "/analysis/:matchId", name: "match-analysis", component: () => import("./pages/AnalysisPage.vue") },
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
