"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Loading } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Team } from "@/lib/types";

export default function TeamsPage() {
  const router = useRouter();
  const { data: teams, error } = useApi<Team[]>("/teams/visible");

  useEffect(() => {
    if (teams?.length === 1) router.replace(`/teams/${teams[0].id}`);
  }, [teams, router]);

  if (!teams) return <Loading error={error} />;
  return (
    <>
      <h1>Teams</h1>
      {teams.length === 0 ? (
        <p className="muted">Bạn chưa thuộc team nào.</p>
      ) : (
        <div className="card">
          <ul>
            {teams.map((t) => (
              <li key={t.id}>
                <Link href={`/teams/${t.id}`}>{t.name}</Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
