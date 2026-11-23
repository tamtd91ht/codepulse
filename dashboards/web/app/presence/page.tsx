"use client";

import { PresenceScreen } from "@/components/Presence";
import { PageHeader } from "@/components/ui";

/* Trạng thái online (docs/specs/33): who is online, away or offline now, and for how long. */
export default function PresencePage() {
  return (
    <>
      <PageHeader title="Trạng thái online" subtitle="Ai đang online, tạm vắng hay offline, và thời lượng từng trạng thái theo ngày." />
      <PresenceScreen />
    </>
  );
}
