import Link from "next/link";
import { FileClock, KeyRound, Settings as SettingsIcon, Users2 } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Admin landing page — entry point to the four management surfaces.
 *
 * Each surface lands in its own sub-PR (C2-C5). Tiles for surfaces
 * that aren't shipped yet stay non-interactive ("Coming in C<n>")
 * so the navigation contract is visible from day one; each
 * subsequent sub-PR flips the corresponding tile to a real link.
 */
interface Tile {
  href: string;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  /** When set, tile renders disabled with a "Coming in sub-Cn" hint. */
  comingIn?: string;
}

const TILES: readonly Tile[] = [
  {
    href: "/admin/users",
    title: "Users",
    description:
      "List members, change roles, suspend accounts, soft-delete inactive users.",
    icon: Users2,
  },
  {
    href: "/admin/audit-log",
    title: "Audit log",
    description:
      "Filter and inspect security-relevant events (logins, password resets, role changes).",
    icon: FileClock,
    comingIn: "C3",
  },
  {
    href: "/admin/api-tokens",
    title: "API tokens",
    description:
      "Instance-wide view of every user's CLI tokens. Revoke if a token leaks.",
    icon: KeyRound,
    comingIn: "C4",
  },
  {
    href: "/admin/settings",
    title: "Instance settings",
    description:
      "Read-only view of the engine's configured CORS origins, OAuth providers, JWT TTL.",
    icon: SettingsIcon,
    comingIn: "C5",
  },
];

export default function AdminLandingPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Administration</h1>
        <p className="text-sm text-muted-foreground">
          Manage the people, tokens, and settings of this DAP instance.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {TILES.map((tile) => (
          <AdminTile key={tile.href} tile={tile} />
        ))}
      </div>
    </div>
  );
}

function AdminTile({ tile }: { tile: Tile }) {
  const Icon = tile.icon;
  const disabled = tile.comingIn !== undefined;
  const card = (
    <Card
      className={cn(
        "h-full transition-colors",
        disabled
          ? "opacity-60"
          : "hover:border-foreground/30 hover:bg-accent/30",
      )}
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Icon className="h-5 w-5 text-muted-foreground" aria-hidden />
          {tile.title}
        </CardTitle>
        <CardDescription>{tile.description}</CardDescription>
      </CardHeader>
      <CardContent>
        {disabled ? (
          <span className="text-xs font-medium text-muted-foreground">
            Coming in sub-{tile.comingIn}
          </span>
        ) : (
          <span className="text-xs font-medium text-primary">Open →</span>
        )}
      </CardContent>
    </Card>
  );
  if (disabled) return card;
  return (
    <Link href={tile.href} className="block">
      {card}
    </Link>
  );
}
