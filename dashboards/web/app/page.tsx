"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Loading } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/** Leads, managers and admins land on their team; everyone else on their own page. */
export default function Home() {
  const router = useRouter();
  const { data: me, error } = useApi<Me>("/auth/me");

  useEffect(() => {
    if (!me) return;
    const led = me.lead_teams[0] ?? me.managed_teams[0];
    router.replace(me.is_admin || led !== undefined ? "/teams" : `/members/${me.id}`);
  }, [me, router]);

  return <Loading error={error} />;
}
