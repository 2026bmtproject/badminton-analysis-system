import type { ServiceStatus } from "./service-manager";

export function canSwitchDirectory(service: ServiceStatus): boolean {
  return service.connected && service.health?.activeTask === null ||
    service.port === null;
}

export function closeAction(service: ServiceStatus): "quit" | "background" {
  return canSwitchDirectory(service) ? "quit" : "background";
}
