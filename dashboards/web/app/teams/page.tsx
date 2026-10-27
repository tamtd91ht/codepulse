"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Icon } from "@/components/icons";
import { EmptyState, Loading, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Team } from "@/lib/types";

export default function TeamsPage() {
  const router = useRouter();
  const { data: teams, error } = useApi<Team[]>("/teams/visible");

  useEffect(() => {
    if (teams?.length === 1) router.replace(`/teams/${teams[0].id}`);
  }, [teams, router]);

  if (!teams || teams.length === 1) return <Loading error={error} />;
  const byId = new Map(teams.map((t) => [t.id, t]));
  return (
    <>
      <PageHeader title="Teams" subtitle="Chọn một team để xem chỉ số tuần/tháng và bảng thành viên." foldable={false} />
      {teams.length === 0 ? (
        <div className="card">
          <EmptyState icon="members" title="Bạn chưa thuộc team nào">
            Admin thêm bạn vào team ở trang Admin → Team &amp; thành viên.
          </EmptyState>
        </div>
      ) : (
        <div className="grid three">
          {teams.map((t) => (
            <Link key={t.id} href={`/teams/${t.id}`} className="card" style={{ display: "flex", gap: 12, alignItems: "center" }}>
              <span className="avatar">
                <Icon name="members" size={16} />
              </span>
              <span style={{ minWidth: 0 }}>
                <strong style={{ display: "block" }}>{t.name}</strong>
                <span className="small muted">
                  {t.parent_id ? `Thuộc ${byId.get(t.parent_id)?.name ?? "team cha"}` : "Team gốc"}
                </span>
              </span>
              <span style={{ marginLeft: "auto", color: "var(--faint)" }}>
                <Icon name="chevronRight" size={16} />
              </span>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
