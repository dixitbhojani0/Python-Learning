import { AnamProvider } from "./AnamProvider";
import type { AvatarProvider } from "./AvatarProvider";
import { DryRunProvider } from "./DryRunProvider";

/** Adapter registry — a new vendor is one new class + one line here. */
const PROVIDERS: Record<string, () => AvatarProvider> = {
  anam: () => new AnamProvider(),
  dry: () => new DryRunProvider(),
};

export function createProvider(name: string): AvatarProvider {
  const factory = PROVIDERS[name];
  if (!factory) throw new Error(`Unknown avatar provider: ${name}`);
  return factory();
}
