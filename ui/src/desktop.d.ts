export {};

declare global {
  interface Window {
    badmintonDesktop?: {
      status(): Promise<DesktopStatus>;
      chooseMatches(): Promise<DesktopStatus>;
      chooseBackend(): Promise<DesktopStatus>;
      chooseUv(): Promise<DesktopStatus>;
    };
  }
}

export type DesktopStatus = {
  desktop: true;
  settings: { matchesDir: string | null; backendDir: string | null; uvBinary: string };
  dataDir: string; hostOrigin: string | null; error: string | null;
  service: null | { connected: boolean; owned: boolean; port: number | null;
    error: string | null; warning: string | null;
    health: null | { activeTask: null | { id: string | null; status: string } } };
};
